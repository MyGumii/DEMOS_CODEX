"""
code_assist_v1 supports two runtime modes.

- Standalone app:
  python -m code_assist_v1.app_code
  http://localhost:10011/

- demos_v1 integration:
  code_assist_v1.blueprint.register_code_blueprint(app)
  mounted at /code/*
"""
