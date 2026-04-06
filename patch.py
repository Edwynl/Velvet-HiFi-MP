import
sys
if
serve_hires_logo
not
in
content:
target
=
@app.get("/")
new_route
=
@app.get("/static/logo-hires.png")\ndef serve_hires_logo():\n    from fastapi.responses import FileResponse\n    from pathlib import Path\n    path = Path(__file__).parent / "static" / "logo-hires.png"\n    return FileResponse(path, media_type="image/png")\n
