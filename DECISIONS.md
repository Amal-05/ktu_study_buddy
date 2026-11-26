# Implementation decisions

- The production build serves the React app and API from one loopback address.
- SQLite keeps subjects, provider settings and page excerpts. Alembic versions the schema.
- The syllabus preview preserves detected assessment text alongside the proposed pattern. The student confirms the edited outline before it affects planning or exams.
- PDF page numbers and PowerPoint slide numbers are physical locations. Word and text files use explicit page breaks because Word pagination varies with layout and fonts.
- Tesseract is a local OCR dependency. Missing or weak OCR is visible in page review.
- The provider router uses documented OpenAI-compatible endpoints. Provider model IDs and capabilities are selected in Settings.
