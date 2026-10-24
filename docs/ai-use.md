# AI Use and Engineering Ownership

## How AI was used

I used Claude, ChatGPT as an engineering assistant throughout the Safe Counter assignment. It supported requirements analysis, architecture exploration, implementation, test development, and documentation. All work was performed against the assignment's synthetic data pack; no real personal or company data was used.

AI assistance covered:
- **Requirements and architecture:** Analysing the nine notes, identifying edge cases, evaluating security boundaries, and documenting design decisions before implementation.
- **Implementation:** Assisting with the PostgreSQL schema, database security, change gate, validation, AI proposal routing, API, CLI, audit chain, and automated tests.
- **Evaluation:** Developing tests for authorization, concurrency, AI failures, hostile output, and audit integrity.
- **Research:** Reviewing provider documentation to assess whether an optional real-LLM experiment would add value beyond the supplied mock.

## Engineering judgment and verification

AI-generated code and recommendations were treated as proposals, not as proof of correctness.

- **Independent verification:** I ran the test suite against real PostgreSQL and the supplied mock AI. Testing exposed a concurrent-approval error-handling bug and an ineffective database-trigger test. Both were corrected, and the tests were rerun.
- **Demonstration integrity:** I corrected demo scenarios that did not actually exercise the claimed failure conditions, ensuring that concurrency and hostile-output behaviour were verified through meaningful tests.
- **Security boundaries:** I retained server-side authorization, validation, approval rules, and the transactional change gate as the enforcement mechanisms. Prompt instructions and model confidence are not treated as security controls.
- **Scope management:** I prioritised a working, tested core over optional model integration and interview preparation.
- **Repository and confidentiality:** I controlled all Git commits and pushes. The confidential assessment PDF was excluded from the repository.

## Limitations and accountability

AI assistance accelerated implementation but did not establish correctness. The resulting system is supported by automated tests and a reproducible demonstration, with known limitations documented in the README and design decisions.

The supplied mock does not evaluate whether few-shot prompting improves a real model's extraction quality. Any real-model experiment will therefore be treated as a separate evaluation, not as evidence that the existing safety controls are effective by themselves.

I am responsible for the submitted design, implementation, trade-offs, and known limitations.