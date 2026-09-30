# MemoryWorks Company Brain architecture

MemoryWorks implements the core premise of [YC's Company Brain thesis](https://www.ycombinator.com/rfs#company-brain): the missing layer between fragmented company data and reliable AI automation is structured, current, executable organizational knowledge—not another search box.

## Closed loop

```text
GitHub / Slack / docs / reports
              │
              ▼
       immutable SourceRevision
              │
              ▼
  chunks + atomic MemoryUnit extraction
              │
              ▼
 MemoryChangeSet (add/update/invalidate/conflict)
              │
              ├──► current company/project/service profiles
              ├──► stale dependent report or brief
              └──► stale compiled SkillSpec
              │
              ▼
 HCAG governed ContextEnvelope
              │
              ▼
     evidence-grounded agent answer/action
```

## Scope and security

The organizational scope graph starts with Workspace → Team → Project and can overlap: multiple teams may share a project, while a source can be restricted to a subset. A source's team grants are copied to every extracted memory.

HCAG applies security trimming before retrieval. The effective context is:

```text
accessible memory
∩ task-relevant scope
∩ temporally current truth
∩ relevant entity graph neighborhood
∩ context budget
```

Unscoped legacy sources remain workspace-visible. Once a team grant exists it is an allow-list. Owners/admins have workspace visibility; members and agents receive only team-authorized sources, memory, profiles, conflicts, change sets, and skill specs.

## Git-like memory history

`SourceRevision` is immutable and content-addressed. `MemoryChangeSet` is the semantic commit produced from a revision. It records:

- added memory;
- updates to older memory;
- invalidated claims that disappeared from the source;
- evidence-backed conflicts requiring review;
- affected profiles, artifacts, and skills.

This separates document history from memory history: a document can change without every sentence becoming company truth, and a memory can be supported by multiple sources.

## Dynamic context

`ContextEnvelope` is the actual context contract between MemoryWorks and an AI system. It records the caller, authorized team IDs, task type, target entities, selected current memories, exact evidence IDs, relevant skills, the source version vector, retrieval trace, token budget, and expiry.

An answer is reproducible because the envelope records both what was selected and which version of each source was current. The retrieval trace explains why HCAG selected the context.

## Reports and executable skills

A report or brief is an `Artifact` with immutable `ArtifactRevision` records, not an untracked output blob. Each revision points to the memories, sources, and context envelope used to generate it. When a dependency changes, MemoryWorks marks the artifact stale and creates an impact record; it never silently rewrites a human-facing report.

`SkillSpec` is a versioned, machine-readable compilation of current procedures, policies, conventions, and decisions. It contains triggers, preconditions, steps, tools, policies, approvals, rollback guidance, and evidence. A relevant change marks it stale so an agent cannot unknowingly follow obsolete company practice.

## Current limitations

Extraction and same-subject reconciliation are conservative and deterministic by default. The model supports future entity-assisted reconciliation, granular information-domain policies, SCIM/group sync, approval workflows for change sets, continuous webhooks, and controlled execution, but those are not required to preserve the current source-backed memory invariant.
