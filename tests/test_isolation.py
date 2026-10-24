"""Level 2A: isolation enforced by the database, for the role the app connects as."""

import psycopg
import pytest

from conftest import H, app_conn


def test_app_role_cannot_bypass_rls():
    with app_conn() as conn:
        role = conn.execute("""SELECT rolsuper, rolbypassrls,
                                      (SELECT tableowner FROM pg_tables WHERE tablename = 'rentals') AS owner,
                                      current_user AS me
                               FROM pg_roles WHERE rolname = current_user""").fetchone()
    assert role["me"] == "sc_app" and role["owner"] != "sc_app"
    assert role["rolsuper"] is False and role["rolbypassrls"] is False


def test_no_shop_set_sees_nothing():
    with app_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM rentals").fetchone()["n"] == 0


def test_cannot_read_or_write_other_shop():
    with app_conn() as conn:
        conn.execute("SELECT set_config('app.shop_id', 'shop_millbrook', false)")
        assert conn.execute("SELECT * FROM rentals WHERE id = 'r_e7001'").fetchall() == []
        assert conn.execute("SELECT count(*) AS n FROM customers WHERE shop_id = 'shop_eastfield'").fetchone()["n"] == 0
        cur = conn.execute("UPDATE rentals SET hire_days = 9 WHERE id = 'r_e7001'")
        assert cur.rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):   # RLS WITH CHECK
            conn.execute("""INSERT INTO notes (shop_id, id, author_id, author_role, written_at, source, text, scope)
                            VALUES ('shop_eastfield', 'X', 'u_dev', 'counter', '2026-10-22T10:00:00+01:00',
                                    'typed', 'x', '{r_e7001}')""")
    with app_conn() as conn:
        conn.execute("SELECT set_config('app.shop_id', 'shop_eastfield', false)")
        assert conn.execute("SELECT hire_days FROM rentals WHERE id = 'r_e7001'").fetchone()["hire_days"] == 3


@pytest.mark.parametrize("stmt", [
    "UPDATE rentals SET shop_id = 'shop_eastfield' WHERE id = 'r_m5004'",
    "UPDATE rentals SET deposit_held_pence = 1 WHERE id = 'r_m5004'",
    "INSERT INTO rentals (id, shop_id, customer_id, item_id, hired_at, hire_days, daily_rate_pence, deposit_held_pence)"
    " VALUES ('r_x', 'shop_millbrook', 'c_m1', 'i_m101', '2026-10-21T10:00:00+01:00', 1, 1, 1)",
    "DELETE FROM rentals WHERE id = 'r_m5004'",
    "INSERT INTO shops VALUES ('shop_x', 'x', 'Europe/London')",
])
def test_app_role_lacks_privileges(stmt):
    with app_conn() as conn:
        conn.execute("SELECT set_config('app.shop_id', 'shop_millbrook', false)")
        with pytest.raises(psycopg.errors.Error):
            conn.execute(stmt)


def test_composite_keys_stop_cross_shop_links():
    from conftest import owner_query
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        owner_query("""INSERT INTO rentals (id, shop_id, customer_id, item_id, hired_at, hire_days,
                                            daily_rate_pence, deposit_held_pence)
                       VALUES ('r_x', 'shop_millbrook', 'c_e1', 'i_m101', '2026-10-21T10:00:00+01:00', 1, 1, 1)""",
                    fetch=False)


def test_api_reads_are_shop_scoped(client):
    assert client.get("/shops/shop_eastfield/records/r_e7001", headers=H("amira")).status_code == 403
    assert client.get("/shops/shop_millbrook/records/r_e7001", headers=H("femi")).status_code == 404
    assert client.get("/shops/shop_eastfield/records/r_e7001", headers=H("femi")).status_code == 200


def test_id_check_ref_hidden_from_non_area_admin(client):
    assert "id_check_ref" not in client.get("/shops/shop_eastfield/records/c_e1", headers=H("ewa")).json()
    assert client.get("/shops/shop_eastfield/records/c_e1", headers=H("femi")).json()["id_check_ref"] == "IDC-EF-0042"
