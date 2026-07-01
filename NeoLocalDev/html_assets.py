GUARDIAN_UNAUTHORIZED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Neo The Guardian LocalDev</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');
:root {
  --bg: #070814;
  --surface: rgba(15, 17, 35, 0.7);
  --border: rgba(239, 68, 68, 0.2);
  --accent: #ef4444;
  --text: #f3f4f6;
  --muted: #9ca3af;
}
body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: 'Outfit', sans-serif;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  overflow: hidden;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 3rem;
  border-radius: 16px;
  max-width: 450px;
  width: 90%;
  text-align: center;
  backdrop-filter: blur(12px);
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
}
.shield {
  font-size: 4rem;
  color: var(--accent);
  margin-bottom: 1rem;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0% { transform: scale(1); filter: drop-shadow(0 0 2px rgba(239,68,68,0.4)); }
  50% { transform: scale(1.05); filter: drop-shadow(0 0 15px rgba(239,68,68,0.8)); }
  100% { transform: scale(1); filter: drop-shadow(0 0 2px rgba(239,68,68,0.4)); }
}
h1 {
  font-weight: 700;
  font-size: 1.8rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #fff 0%, #fca5a5 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
p {
  color: var(--muted);
  font-size: 0.95rem;
  line-height: 1.5;
  margin-bottom: 2rem;
}
.ip-box {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.05);
  padding: 0.75rem;
  border-radius: 8px;
  font-family: monospace;
  font-size: 1.1rem;
  color: #fca5a5;
  margin-bottom: 2rem;
}
.btn {
  background: var(--accent);
  color: white;
  border: none;
  padding: 0.85rem 2rem;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
  transition: all 0.3s;
}
.btn:hover {
  background: #dc2626;
  box-shadow: 0 0 15px rgba(239,68,68,0.4);
}
.btn:disabled {
  background: rgba(255,255,255,0.1);
  color: var(--muted);
  cursor: not-allowed;
}
</style>
</head>
<body>
<div class="card">
  <div class="shield">🛡️</div>
  <h1>Neo The Guardian</h1>
  <p>Your device is not approved to access this local environment.</p>
  <div class="ip-box" id="ipBox">IP: {client_ip}</div>
  <button class="btn" id="reqBtn" onclick="requestAccess()">Request Access</button>
</div>

<script>
const urlParams = new URLSearchParams(window.location.search);
const fromUrl = urlParams.get('from');

async function requestAccess() {
  const btn = document.getElementById('reqBtn');
  btn.disabled = true;
  btn.textContent = 'Sending Request...';
  
  try {
    const res = await fetch('/guardian/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip: '{client_ip}' })
    });
    const data = await res.json();
    if (data.ok) {
      btn.textContent = 'Request Sent ✔';
      btn.style.background = '#10b981';
      document.querySelector('p').textContent = 'Request sent successfully. Please ask the developer to approve your device in their dashboard.';
      startPolling();
    } else {
      btn.textContent = 'Failed to send request';
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = 'Connection Error';
    btn.disabled = false;
  }
}

function startPolling() {
  setInterval(async () => {
    try {
      const res = await fetch('/guardian/check');
      const data = await res.json();
      if (data.ok && data.approved) {
        if (fromUrl) {
          window.location.href = fromUrl;
        } else {
          window.location.reload();
        }
      }
    } catch (e) {}
  }, 2000);
}

// Start polling immediately in case it was already approved in the background
startPolling();
</script>
</body>
</html>"""

GUARDIAN_APPROVED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Access Approved - Neo LocalDev</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');
:root {
  --bg: #070814;
  --surface: rgba(15, 17, 35, 0.7);
  --border: rgba(16, 185, 129, 0.2);
  --accent: #10b981;
  --text: #f3f4f6;
  --muted: #9ca3af;
}
body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: 'Outfit', sans-serif;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  overflow: hidden;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 3rem;
  border-radius: 16px;
  max-width: 450px;
  width: 90%;
  text-align: center;
  backdrop-filter: blur(12px);
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
}
.shield {
  font-size: 4rem;
  color: var(--accent);
  margin-bottom: 1rem;
}
h1 {
  font-weight: 700;
  font-size: 1.8rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #fff 0%, #a7f3d0 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
p {
  color: var(--muted);
  font-size: 0.95rem;
  line-height: 1.5;
  margin-bottom: 2rem;
}
.ip-box {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.05);
  padding: 0.75rem;
  border-radius: 8px;
  font-family: monospace;
  font-size: 1.1rem;
  color: #a7f3d0;
  margin-bottom: 2rem;
}
.btn {
  background: var(--accent);
  color: white;
  border: none;
  padding: 0.85rem 2rem;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
  text-decoration: none;
  display: inline-block;
  box-sizing: border-box;
  transition: all 0.3s;
}
.btn:hover {
  background: #059669;
  box-shadow: 0 0 15px rgba(16,185,129,0.4);
}
</style>
</head>
<body>
<div class="card">
  <div class="shield">🛡️</div>
  <h1>Device Approved!</h1>
  <p>Your device is fully approved to access this local environment. Redirecting you back to your application...</p>
  <div class="ip-box">IP: {client_ip}</div>
  <a href="https://dev.local/admin/" class="btn">Go to Dashboard</a>
</div>

<script>
const urlParams = new URLSearchParams(window.location.search);
const fromUrl = urlParams.get('from');
if (fromUrl) {
  setTimeout(() => {
    window.location.href = fromUrl;
  }, 1000);
}
</script>
</body>
</html>"""
