# Implementation decisions

- The production build serves the React app and API from one loopback address.
- SQLite keeps subjects, provider settings and page excerpts. Alembic versions the schema.
- The syllabus preview preserves detected assessment text alongside the proposed pattern. The student confirms the edited outline before it affects planning or exams.
- PDF page numbers and PowerPoint slide numbers are physical locations. Word and text files use explicit page breaks because Word pagination varies with layout and fonts.
- Tesseract is a local OCR dependency. Missing or weak OCR is visible in page review.
- The provider router uses documented OpenAI-compatible endpoints. Provider model IDs and capabilities are selected in Settings.
- For paper scoring, a matched topic counts once per paper. Dated older-scheme papers contribute reduced weight only for overlapping topics. Unknown years and schemes remain visible.
- The sample is original instructional material. Mock AI uses the bundled examples and retrieved excerpts, and mock web search returns no results.
- sqlite-vec stores the local embedding index. Lexical matching remains available offline when model weights are missing.
- The note queue stores progress in SQLite and resumes unfinished jobs when the app restarts. Regeneration keeps all revisions and preserves the active student edit.
