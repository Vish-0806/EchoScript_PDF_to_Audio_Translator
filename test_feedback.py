import os, glob
from app import app

# Ensure email env vars are not set to force local save fallback
os.environ.pop('EMAIL_USER', None)
os.environ.pop('EMAIL_PASS', None)

app.testing = True
client = app.test_client()

rv_get = client.get('/feedback')
print('GET /feedback status:', rv_get.status_code)

rv = client.post('/feedback', data={'name':'Unit Test','email':'unit@example.com','message':'Hello from test'}, follow_redirects=True)
print('POST /feedback status:', rv.status_code)

body = rv.get_data(as_text=True)
print('Response contains success title:', 'Transmission Received' in body)

feedback_dir = os.path.join(app.instance_path, 'feedbacks')
print('Instance path:', app.instance_path)
print('Feedback dir path:', feedback_dir)

if os.path.exists(feedback_dir):
    files = sorted(glob.glob(os.path.join(feedback_dir, 'feedback_*.txt')))
    print('Saved feedback files count:', len(files))
    if files:
        latest = files[-1]
        print('Latest file:', latest)
        print('--- file content ---')
        print(open(latest, 'r', encoding='utf-8').read())
else:
    print('Feedback dir does not exist')
