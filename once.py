from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    'client_secret.json', # Rename your downloaded file to match this
    scopes=['https://www.googleapis.com/auth/youtube.upload']
)
creds = flow.run_local_server(port=0)

with open('yt_token.json', 'w') as token_file:
    token_file.write(creds.to_json())
print("Saved yt_token.json!")
