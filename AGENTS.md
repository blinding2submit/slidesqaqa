# Agent Instructions

## Project Overview & Architecture
- The project is a Flask application designed for analyzing PDF presentation decks using the Gemini API.
- The live application is deployed on Google Cloud Run at `https://slidesqaqa-974767694043.us-west1.run.app/`.
- The application's core logic inside `flask-app.py` features a multi-stage LLM processing pipeline (Window Planning, Deck Synthesis, Slide Annotation, Reconciliation) and utilizes Pydantic data schemas to structure the generative output.
- The application's frontend HTML templates and CSS styling are embedded directly within the main routing file (`flask-app.py`) using `render_template_string`, rather than existing in a separate `templates/` or `static/` directory.
- The frontend relies on vanilla JavaScript and CSS directly embedded within the HTML template in `flask-app.py`, without utilizing external frontend frameworks like React or Vue.

## Setup and Run Server
- The standard environment setup and execution process is to create a virtual environment (use `python3 -m venv .venv` as `virtualenv` may not be installed in the environment), install dependencies from `requirements.txt`, and run the server using `python flask-app.py`, which binds to port 8080.
- If PyMuPDF/fitz is missing during execution, install it explicitly with `python3 -m pip install PyMuPDF`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python flask-app.py &
SERVER_PID=$!
sleep 7
curl -siv http://127.0.0.1:8080/
```

## Testing
- We highly suggest writing Playwright tests to perform frontend verification and test application flows. Playwright and its browsers need to be installed in the virtual environment.
- The test environment setup requires: `pip install -r requirements.txt`, followed by `python3 -m pip install playwright pytest-playwright && playwright install chromium`.
- The tests can be reliably executed using `python3 -m pytest tests/`.

- **Important:** To execute the UI tests successfully, the Flask application must first be running in the background (e.g., `python3 flask-app.py &`) because the Playwright tests connect to `http://127.0.0.1:8080/`.
- Your development environment may or may not have a working Gemini API key, so only try run tests on `.pdf` files when absolutely necessary; e.g. not for landing page `GET /` UI changes. You can check to see whether a `GEMINI_API_KEY` environment variable is set, and use it to fill in the form field when using Playwright; if it isn't, you will need to ask the user to set the envar.
- Do not run tests if modifications are strictly limited to markdown (`*.md`) files.
- Playwright UI test outputs (such as verification screenshots and videos) should be saved to the `test-outputs/` directory, which must remain git-ignored to prevent polluting the repository.

## LaTeX Compilation
- Compiling LaTeX documents (`.tex` to `.pdf`) in this environment requires first installing dependencies via:
  ```bash
  sudo apt-get update && sudo apt-get install -y texlive-latex-base texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra
  ```
- After installation, run `pdflatex` on the `.tex` file.

## Coding Guidelines
- **Security:** Never expose server environment variables (especially sensitive ones like API keys) directly to the client UI. Ensure they are masked or kept entirely server-side.
- **UI Preferences:** For web UI layouts and summaries, the user prefers semantic HTML/CSS structures that copy cleanly into Markdown format over relying on actual slide graphics or images.
- **Documentation:** The user prefers heavily commented code, including comprehensive docstrings, same-line import comments, and explicitly documented constants.
