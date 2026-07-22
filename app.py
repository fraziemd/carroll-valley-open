# Streamlit Community Cloud launches this repo's app from `app.py` (the
# entrypoint set when the app was first deployed, and NOT editable afterwards).
# The real 2026 application lives in `app_2026.py`, so this file just hands off
# to it. That lets the existing deployment (carrollvalleyopen.streamlit.app)
# serve the 2026 app at the same URL with no Streamlit-side reconfiguration.
#
# We use runpy.run_path (rather than `import app_2026`) on purpose: Streamlit
# re-executes the entrypoint script on every rerun, and a plain import would be
# cached after the first run (so the app body / main() would never run again).
# run_path re-executes app_2026.py from scratch each time, exactly as if
# Streamlit were running it directly. run_name="__main__" triggers app_2026.py's
# `if __name__ == '__main__': main()` guard.
import runpy

runpy.run_path("app_2026.py", run_name="__main__")
