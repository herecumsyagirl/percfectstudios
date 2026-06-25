import sys
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import urllib.parse
import json
import requests
import time
import random
import string

ADDON     = xbmcaddon.Addon()
ADDON_ID  = ADDON.getAddonInfo('id')
BASE_URL  = 'https://percfectstudios.onrender.com'
HANDLE    = int(sys.argv[1]) if len(sys.argv) > 1 else 0

# ── Settings helpers ──────────────────────────────────────

def get_api_key():
    return ADDON.getSetting('api_key')

def set_api_key(key):
    ADDON.setSetting('api_key', key)

def get_username():
    return ADDON.getSetting('username')

def set_username(name):
    ADDON.setSetting('username', name)

# ── API helpers ───────────────────────────────────────────

def api_headers():
    return {'X-API-Key': get_api_key(), 'Content-Type': 'application/json'}

def get_credits():
    try:
        r = requests.get(f'{BASE_URL}/api/kodi/credits', headers=api_headers(), timeout=8)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {'image_credits': 0, 'video_credits': 0}

# ── Activation flow ───────────────────────────────────────

def activate():
    """Generate a device code, show QR + PIN prompt, poll until phone activates."""
    device_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    activate_url = f'{BASE_URL}/kodi-activate?device={device_code}'

    # Show QR code dialog
    dialog = xbmcgui.Dialog()

    # We show the URL in a progress dialog while polling
    # Kodi doesn't have native QR rendering, so we display the URL
    # and a message to scan with phone camera
    progress = xbmcgui.DialogProgress()
    progress.create(
        'PercfectStudios — Connect your TV',
        'Scan the QR code with your phone OR go to:\n\n'
        f'  [B]{activate_url}[/B]\n\n'
        'Waiting for you to sign up on your phone...'
    )

    # Show QR via a background image (generated server-side)
    qr_image_url = f'https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(activate_url)}'
    xbmc.executebuiltin(f'ShowPicture({qr_image_url})')

    # Poll for activation
    for i in range(200):  # poll for up to ~10 min
        if progress.iscanceled():
            progress.close()
            xbmc.executebuiltin('Action(Back)')
            return False

        time.sleep(3)
        percent = int((i / 200) * 100)
        progress.update(percent)

        try:
            r = requests.get(f'{BASE_URL}/api/kodi/poll/{device_code}', timeout=5)
            if r.ok:
                data = r.json()
                status = data.get('status')
                if status == 'activated':
                    progress.close()
                    set_api_key(data['api_key'])
                    set_username(data.get('username', ''))
                    xbmcgui.Dialog().ok(
                        'Connected!',
                        f'Welcome, [B]{data.get("username")}[/B]!\n\n'
                        f'[B]{data.get("image_credits", 0)}[/B] image credits\n'
                        f'[B]{data.get("video_credits", 0)}s[/B] video time'
                    )
                    return True
                elif status == 'expired':
                    progress.close()
                    dialog.ok('Code Expired', 'Your QR code expired. Please try again.')
                    return False
        except Exception:
            pass

    progress.close()
    dialog.ok('Timed Out', 'Connection timed out. Please try again.')
    return False


def verify_pin():
    """Manual PIN entry — user scanned QR on phone, got a PIN, types it here."""
    dialog = xbmcgui.Dialog()
    device_code = dialog.input('Enter device code (shown on phone after scanning)', type=xbmcgui.INPUT_ALPHANUM)
    if not device_code:
        return False
    pin = dialog.input('Enter your 6-digit PIN', type=xbmcgui.INPUT_NUMERIC)
    if not pin:
        return False

    try:
        r = requests.post(f'{BASE_URL}/api/kodi/verify',
                          json={'device_code': device_code, 'pin': pin},
                          timeout=10)
        if r.ok:
            data = r.json()
            set_api_key(data['api_key'])
            set_username(data.get('username', ''))
            dialog.ok('Connected!',
                       f'Welcome, [B]{data.get("username")}[/B]!\n\n'
                       f'[B]{data.get("image_credits", 0)}[/B] image credits\n'
                       f'[B]{data.get("video_credits", 0)}s[/B] video time')
            return True
        else:
            dialog.ok('Error', r.json().get('error', 'Authentication failed.'))
    except Exception as e:
        dialog.ok('Error', str(e))
    return False

# ── Credit display ────────────────────────────────────────

def credits_header(credits):
    img_c = credits.get('image_credits', 0)
    vid_c = credits.get('video_credits', 0)
    return f'[B]{img_c}[/B] images · [B]{vid_c}s[/B] video'

# ── Generate image ────────────────────────────────────────

def generate_image():
    credits = get_credits()
    if credits.get('image_credits', 0) <= 0:
        show_buy_credits('You have no image credits left.')
        return

    dialog = xbmcgui.Dialog()
    prompt = dialog.input('Describe your image', type=xbmcgui.INPUT_ALPHANUM)
    if not prompt:
        return

    progress = xbmcgui.DialogProgress()
    progress.create('PercfectStudios', 'Generating your image...')
    progress.update(20)

    try:
        r = requests.post(f'{BASE_URL}/api/generate/image',
                          json={'prompt': prompt},
                          headers=api_headers(), timeout=60)
        progress.update(90)
        if r.ok:
            url = r.json().get('url')
            progress.close()
            if url:
                xbmc.executebuiltin(f'ShowPicture({url})')
        else:
            progress.close()
            err = r.json().get('error', 'Generation failed.')
            if '402' in str(r.status_code) or 'credit' in err.lower():
                show_buy_credits(err)
            else:
                dialog.ok('Error', err)
    except Exception as e:
        progress.close()
        dialog.ok('Error', str(e))

# ── Generate video ────────────────────────────────────────

def generate_video():
    credits = get_credits()
    vid_c = credits.get('video_credits', 0)

    if vid_c <= 0:
        show_buy_credits('You have no video seconds left.')
        return

    dialog = xbmcgui.Dialog()

    # Duration picker
    durations = ['5 seconds', '6 seconds', '10 seconds', '15 seconds']
    dur_secs  = [5, 6, 10, 15]
    idx = dialog.select(f'Video length ({vid_c}s available)', durations)
    if idx < 0:
        return
    duration = dur_secs[idx]

    if vid_c < duration:
        show_buy_credits(f'You need {duration}s but only have {vid_c}s.\nBuy more credits to continue.')
        return

    prompt = dialog.input('Describe your video', type=xbmcgui.INPUT_ALPHANUM)
    if not prompt:
        return

    progress = xbmcgui.DialogProgress()
    progress.create('PercfectStudios', f'Generating {duration}s video...\nThis takes about 60–120 seconds.')

    # Animate progress while waiting
    for pct in range(10, 85, 3):
        if progress.iscanceled():
            progress.close()
            return
        time.sleep(2)
        progress.update(pct, f'Generating {duration}s video... {pct}%')

    try:
        r = requests.post(f'{BASE_URL}/api/generate/video',
                          json={'prompt': prompt, 'duration': duration},
                          headers=api_headers(), timeout=180)
        progress.update(95)
        if r.ok:
            url = r.json().get('url')
            progress.close()
            if url:
                li = xbmcgui.ListItem(prompt, path=url)
                li.setInfo('video', {'title': prompt})
                xbmc.Player().play(url, li)
        else:
            progress.close()
            err = r.json().get('error', 'Generation failed.')
            if '402' in str(r.status_code) or 'credit' in err.lower():
                show_buy_credits(err)
            else:
                dialog.ok('Error', err)
    except Exception as e:
        progress.close()
        dialog.ok('Error', str(e))

# ── Buy credits ───────────────────────────────────────────

def show_buy_credits(message=''):
    dialog = xbmcgui.Dialog()
    buy_url = f'{BASE_URL}/buy-credits'
    qr_url  = f'https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={urllib.parse.quote(buy_url)}'

    choice = dialog.select(
        'Get More Credits',
        [
            '📱 Scan QR code with your phone (fastest)',
            f'⌨  Type this URL: percfectstudios.onrender.com/buy-credits',
        ]
    )
    if choice == 0:
        # Show QR code
        xbmc.executebuiltin(f'ShowPicture({qr_url})')
        dialog.ok(
            'Scan to Buy Credits',
            f'{message}\n\n'
            'Scan the QR code on screen with your phone to buy credits.\n'
            'Your balance updates automatically!'
        )
    elif choice == 1:
        dialog.ok(
            'Buy Credits',
            f'{message}\n\n'
            'On your phone or computer, go to:\n\n'
            '[B]percfectstudios.onrender.com/buy-credits[/B]\n\n'
            'Your credit balance updates automatically after payment.'
        )

# ── Gallery ───────────────────────────────────────────────

def show_gallery():
    try:
        r = requests.get(f'{BASE_URL}/api/gallery', headers=api_headers(), timeout=10)
        if not r.ok:
            xbmcgui.Dialog().ok('Error', 'Could not load gallery.')
            return
        items = r.json()
    except Exception as e:
        xbmcgui.Dialog().ok('Error', str(e))
        return

    xbmcplugin.setPluginCategory(HANDLE, 'My Generations')
    xbmcplugin.setContent(HANDLE, 'videos')

    for item in items:
        url = item.get('output_url', '')
        kind = item.get('type', 'image')
        prompt = item.get('prompt', 'Untitled')
        li = xbmcgui.ListItem(label=f'[{kind.upper()}] {prompt[:60]}')
        li.setArt({'thumb': url if kind == 'image' else ''})
        li.setInfo('video', {'title': prompt, 'mediatype': 'video'})
        li.setProperty('IsPlayable', 'true')
        xbmcplugin.addDirectoryItem(HANDLE, url, li, False)

    xbmcplugin.endOfDirectory(HANDLE)

# ── Main menu ─────────────────────────────────────────────

def main_menu():
    api_key = get_api_key()

    if not api_key:
        # Not authenticated
        dialog = xbmcgui.Dialog()
        choice = dialog.select(
            'Welcome to PercfectStudios',
            [
                '📱 Scan QR code to sign up / log in (recommended)',
                '⌨  Enter PIN manually (already scanned on phone)',
            ]
        )
        if choice == 0:
            if activate():
                xbmc.executebuiltin('Container.Refresh')
        elif choice == 1:
            if verify_pin():
                xbmc.executebuiltin('Container.Refresh')
        return

    # Authenticated — show menu with live credits
    credits = get_credits()
    header = credits_header(credits)
    username = get_username() or 'You'

    xbmcplugin.setPluginCategory(HANDLE, f'PercfectStudios — {header}')
    xbmcplugin.setContent(HANDLE, 'videos')

    items = [
        ('🖼  Generate Image',  'generate_image'),
        ('🎬  Generate Video',  'generate_video'),
        ('📁  My Gallery',      'gallery'),
        ('💳  Buy Credits',     'buy_credits'),
        ('🔄  Refresh Credits', 'refresh'),
        ('🔓  Disconnect',      'disconnect'),
    ]

    for label, action in items:
        li = xbmcgui.ListItem(label=label)
        li.setProperty('IsPlayable', 'false')
        url = f'plugin://{ADDON_ID}/?action={action}'
        xbmcplugin.addDirectoryItem(HANDLE, url, li, True)

    xbmcplugin.endOfDirectory(HANDLE)


# ── Router ────────────────────────────────────────────────

params = {}
if len(sys.argv) > 2 and sys.argv[2]:
    params = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?')))

action = params.get('action', '')

if action == 'generate_image':
    generate_image()
elif action == 'generate_video':
    generate_video()
elif action == 'gallery':
    show_gallery()
elif action == 'buy_credits':
    show_buy_credits()
elif action == 'refresh':
    xbmc.executebuiltin('Container.Refresh')
    main_menu()
elif action == 'disconnect':
    if xbmcgui.Dialog().yesno('Disconnect', 'Remove your account from this device?'):
        set_api_key('')
        set_username('')
        xbmc.executebuiltin('Container.Refresh')
else:
    main_menu()
