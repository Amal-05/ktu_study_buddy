from .main import app, mount_frontend
from . import accounts, chat, exams, oauth, practice, sample_data, study

for feature in (accounts, oauth, chat, study, exams, practice, sample_data):
    app.include_router(feature.api)

mount_frontend()
