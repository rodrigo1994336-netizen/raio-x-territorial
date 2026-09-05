from pathlib import Path

src=Path('portal_pdf_v21.py').read_text(encoding='utf-8')
assert 'TRANSIENT_STATUS={502,503,504}' in src
assert 'retries=2' in src
assert 'RX_PORTAL_WORKER_RETRY=' in src
assert 'RX_PORTAL_PDF_V43_8_2=' in src
print('RX_V43_8_2_WORKER_RETRY_CONTRACT=ok')
