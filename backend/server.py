from .main import app, mount_frontend
from . import accounts, chat, exams, practice, sample_data, study

for feature in (accounts, chat, study, exams, practice, sample_data):
    app.include_router(feature.api)

mount_frontend()
