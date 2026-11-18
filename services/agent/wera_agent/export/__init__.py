"""The CV file (EXP-01, 18 Nov): a RenderCV document per pack, and the PDF rendered from it.

`cv_yaml` builds, checks and reads the document and holds no contact details; `cv_pdf` renders
it, and is the one place in the agent where contact details enter a document (CV-06, EXP-02).
The cover letter and both Word files are still rendered by the web app (`lib/server/export`).
"""
