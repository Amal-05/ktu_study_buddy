from .main import app, mount_frontend
from . import chat, exams, practice, sample_data, study

for feature in (chat, study, exams, practice, sample_data):
    app.include_router(feature.api)

mount_frontend()
