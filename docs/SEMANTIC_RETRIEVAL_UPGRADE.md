# Grounded semantic retrieval upgrade

Investor explanation: **MemoryWorks moved from keyword search to grounded semantic retrieval with cited, confidence-scored answers.**

## What changed

- Source content is split into line-addressable 300–800-token contextual chunks with overlap. GitHub citations resolve to the indexed commit and exact line range.
- ArcadeDB remains the project-scoped vector store. Production and Docker use `BAAI/bge-small-en-v1.5` locally by default; `text-embedding-3-large` can be selected with `ORGMEMORY_EMBEDDING_PROVIDER=openai`. Hash vectors remain an explicitly degraded test fallback and production refuses that configuration.
- Retrieval combines semantic cosine similarity, sparse and structural evidence, HCAG temporal memory, source authority, and a `ms-marco-MiniLM-L-6-v2` cross-encoder rerank. Canonical structured sources also expose an auditable `structural_relevance` signal so manifests are not penalized merely for not reading like prose. Confidence is calculated from those observed signals; it is not a UI constant.
- The answer contract exposes exact sanitized chunks, semantic and rerank scores, repository scope, commits, sections, and source links through **Why this answer**.
- GitHub ingestion includes repository files, repository metadata, the latest 50 commit messages, issue discussions, PR descriptions, review comments, and reviews.
- Repository refresh is a content-hash delta: unchanged sources retain their items and vectors, changed sources are replaced individually, and deleted sources are removed. It no longer deletes the entire project corpus.
- Secret values are redacted before SQLite, ArcadeDB, embedding, audit, and answer generation. Environment and configuration schemas preserve variable names and documented purpose only.
- Reliability review auto-suggests owners from CODEOWNERS, last-file committer, evidence owner, repository committer, or repository namespace; supports bulk review; and can auto-verify unchanged commit-backed assertions after `ASSERTION_AUTO_VERIFY_DAYS`.

## Runtime controls

| Setting | Docker default | Purpose |
|---|---|---|
| `ORGMEMORY_EMBEDDING_PROVIDER` | `fastembed` | `fastembed`, `openai`, or degraded `deterministic` for tests |
| `ORGMEMORY_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Private local dense embedding model |
| `ORGMEMORY_RERANKER_PROVIDER` | `fastembed` | Enables local cross-encoder reranking |
| `ORGMEMORY_RERANKER_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | Candidate reranker |
| `ASSERTION_AUTO_VERIFY_ENABLED` | `true` | Enables unchanged-evidence stability policy |
| `ASSERTION_AUTO_VERIFY_DAYS` | `7` | Undisputed interval before eligible auto-verification |

AgentGate remains unchanged: every external or mutating operational action still requires its existing approval boundary.

The older `RUNBOOK_*` names for these settings are still accepted as fallbacks.
