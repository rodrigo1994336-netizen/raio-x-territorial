import os

# Render's existing public app service starts `portal_api:app`. Keep that stable URL
# while loading the V8 extension only on the operational portal service.
if os.getenv('RX_RELEASE') == 'V8_OPERATIONAL_ZERO_COST':
    try:
        import portal_v8  # noqa: F401
        print('RX_PORTAL_V8_EXTENSION=loaded', flush=True)
    except Exception as exc:
        print(f'RX_PORTAL_V8_EXTENSION=failed:{type(exc).__name__}:{str(exc)[:300]}', flush=True)
