# Branch Plan

## Create Now

- `develop` - integration branch for active development before merging into `main`.
- `feature/langgraph-agent-refactor` - current work to convert agents into LangGraph-compatible nodes.

## Create Later As Needed

- `feature/evidence-agents` - implement device, comms, consortium, delivery photo, and order timeline agents.
- `feature/scoring-engine` - build dispute win probability, expected value logic, and ML integration.
- `feature/rebuttal-generation` - build rebuttal packet generation, PDF output, and playbook logic.
- `feature/provider-integrations` - implement Razorpay, Shiprocket, Freshdesk, SEON, Ethoca, Verifi, Stripe, and other external clients.
- `feature/api-routes` - implement FastAPI dispute routes, merchant routes, schemas, and webhooks.
- `feature/neo4j-graph-db` - implement Neo4j client, customer history queries, and fraud pattern graph queries.
- `feature/tests-and-fixtures` - improve fixtures, unit tests, graph tests, and integration tests.

## Support Branches

- `hotfix/<short-issue-name>` - urgent fixes based from `main`.
- `chore/project-setup` - repository setup, `.gitignore`, CI, formatting, Docker, and dependency cleanup.
