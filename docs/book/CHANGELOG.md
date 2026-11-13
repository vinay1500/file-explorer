# Change log

Appended, never edited. When something in this book turns out to be wrong, the original
statement stays and a new row explains what is true instead — with the date it changed.

| Date | Change | Why |
|---|---|---|
| 2025-04-18 | Project started as two repositories — Flask API and a Create React App frontend | Original build |
| 2025-04-21 | Deployed: API on Render, frontend on Vercel, PostgreSQL on Render's free tier | Original build |
| ~2025-06 | Database became unreachable; the demo silently served an empty tree | Cause established at M0; see [chapter 7](07-the-silent-outage.md) |
| 2026-03-28 | Two commits removing `__pycache__` files | Last activity before the rebuild |
| 2026-11-13 | Audit: 24 findings recorded, each tagged by how it is known. One claim in the prior review corrected | Evidence gathered before writing any code |
| 2026-11-13 | **M0 complete.** Demo link withdrawn, service suspended, public DDL endpoint removed, `.gitignore` added | Stop ongoing harm before building |
| 2026-11-13 | Governance docs restructured — `CONTRIBUTING.md` published, working notes kept local | Nothing committed should read as machine-generated |
| 2026-11-13 | **M1 graft.** Both repositories merged into one, preserving all 30 original commits. A committed virtualenv was removed from every commit, taking the repository from 12 MB to 144 KB | One repo is a precondition for integration CI and end-to-end tests |
| 2026-11-13 | Commit authorship normalised to a single identity across all 36 commits | Done before the first push, while it was still free |
| 2026-11-13 | Decision: no local runtime. Tests run in GitHub Actions against a service-container PostgreSQL | Nothing installs on the development machine |
| 2026-11-13 | Chapter 7 written | M0 made it true |
