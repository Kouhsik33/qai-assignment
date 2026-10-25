# AI Use and Engineering Ownership

## How AI was used

I used Claude, ChatGPT as an engineering assistant throughout the Safe Counter assignment. It supported requirements analysis, architecture exploration, implementation, test development, and documentation. All work was performed against the assignment's synthetic data pack; no real personal or company data was used.

AI assistance covered:
- **Requirements and architecture:** Analysing the nine notes, identifying edge cases, evaluating security boundaries, and documenting design decisions before implementation.
- **Implementation:** Assisting with the PostgreSQL schema, database security, change gate, validation, AI proposal routing, API, CLI, audit chain, and automated tests.
- **Evaluation:** Developing tests for authorization, concurrency, AI failures, hostile output, and audit integrity.
- **Research:** Reviewing provider documentation to assess whether an optional real-LLM experiment would add value beyond the supplied mock.
- **Level 3 work:** Implementing the optional Groq adapter and the evaluation that compared the mock with zero-shot and few-shot prompting, transactional-outbox event delivery to the pack's flaky receiver, and the scribe agent that holds a single-use, proposal-only task token.

## Engineering judgment and verification

AI-generated code and recommendations were treated as proposals, not as proof of correctness.

- **Independent verification:** I ran the test suite against real PostgreSQL and the supplied mock AI. Testing exposed a concurrent-approval error-handling bug and an ineffective database-trigger test. Both were corrected, and the tests were rerun.
- **Demonstration integrity:** I corrected demo scenarios that did not actually exercise the claimed failure conditions, ensuring that concurrency and hostile-output behaviour were verified through meaningful tests.
- **Security boundaries:** I retained server-side authorization, validation, approval rules, and the transactional change gate as the enforcement mechanisms. Prompt instructions and model confidence are not treated as security controls.
- **Scope management:** I prioritised a working, tested core over optional model integration and interview preparation, and accepted the real-model experiment only after the required levels were verified.
- **Honest evaluation:** I required the experiment's decision rules to be written down before any real-model run, kept the mock as the default provider, and kept the API key in a local, git-ignored `.env`.
- **Repository and confidentiality:** I controlled all Git commits and pushes. The confidential assessment PDF was excluded from the repository.

## Limitations and accountability

AI assistance accelerated implementation but did not establish correctness. The resulting system is supported by automated tests and a reproducible demonstration, with known limitations documented in the README and design decisions.

The supplied mock cannot show whether few-shot prompting helps a real model, so I ran a separate experiment with Groq `openai/gpt-oss-120b` on the nine notes (three runs per condition). Few-shot scored higher than zero-shot on this small set, but the model still followed the injected instructions in N08 in five of six runs, and only the server's checks stopped them; the same three wrong-but-valid proposals reached a person in every run. I treat these as measurements of this setup on nine notes, not as general evidence about the model or as proof that the safety controls are sufficient.

I am responsible for the submitted design, implementation, trade-offs, and known limitations.