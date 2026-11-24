# EduBridge

EduBridge is a course library you open in a browser. A teacher and a student each create an account, then the teacher sends a signed file to the student's username. They do not need to be on the same Wi-Fi.

One computer runs the app. Phones, tablets, and other computers open the public HTTPS address.

## Start

Install Python 3, then from this folder:

```bat
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
run.bat
```

On macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x run.sh
./run.sh
```

`run.bat` and `run.sh` start the web app and try to print a public HTTPS address. The same address is shown at the top of the page. Use **Copy address** to share it.

That address lasts while EduBridge stays open. The next start gets a new temporary address.

On this computer the page is also at `http://127.0.0.1:8080`.

## Send a signed file

1. Teacher: open the address, create an account with the role Teacher, add a file, then under Signatures create a signing key and sign the file.
2. Student: open the same address and create an account. Under Signatures, import the teacher's public key and compare the fingerprint with the teacher.
3. Teacher: open Share, start typing the student's name or username, choose the account, and select **Send selected file to username**.
4. Student: accept the file. It appears in their library as Signed.

Only a file with a trusted instructor signature can be sent as official material. The private key stays on the computer that is running EduBridge.

## Keep one public address

To stop the address from changing on every start, create a Cloudflare tunnel whose public hostname points at `http://127.0.0.1:8080`. Save two files next to `run.bat`:

- `tunnel.token` — the tunnel token
- `public_url.txt` — the fixed address, such as `https://school.example.com`

Start EduBridge again. The page shows **Fixed address**. Those files stay on this computer and are not meant to be committed.

You can also set `EDUBRIDGE_TUNNEL_TOKEN` and `EDUBRIDGE_PUBLIC_URL`.

## Optional same-network transfer

Share includes **Optional: same Wi-Fi device transfer**. That path is not required. Username sending is the way to share a signed file.

The desktop window (`python edubridge.py --ui tk`) is the older local library. Use the browser for accounts.

## Data

Accounts, files, and signing keys are stored on the computer that runs EduBridge:

- Windows: `%LOCALAPPDATA%\EduBridge`
- macOS: `~/Library/Application Support/EduBridge`
- Linux: `~/.local/share/EduBridge`

Set `EDUBRIDGE_HOME` to use a different folder.
