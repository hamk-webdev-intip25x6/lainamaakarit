## AI Agent Configuration

This project uses `.agentignore` to prevent AI agents from accessing for example:
- Environment files (.env*)
- Credentials (*.pem, credentials.json)
- Build artifacts (dist/, node_modules/)
Do not use the folder listed inside it.

## Goal

A simple library web app for PDF files using mainly django and python. It aims for ease of use and it should also work on mobile devices.

Django and pillow have been installed on .venv using venv.
Use build-in Django development server, on the default prt 8000.

## Principles

KISS, DRY, SOLID, YAGNI. Don't install dependencies unless the user approves.

Used sqlite3 as the database.
All the Django templates should inherit from base.html, witch is responsive and has dark/light mode switch and special high contrast mode.
Don't use external dependencies without comparing them and then asking the user first.


## Testing

Comprehensive unit tests, using Finnish language, but also few sentences of Simplified Chinese, Arabic and other site breaking characters.