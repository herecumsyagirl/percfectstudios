import sys
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import urllib.parse
import urllib.request
import urllib.error
import json
import os
import base64
import mimetypes
import time
import random
import string

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
BASE_URL = 'https://percfectai.com'
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 else 0


def get_api_key():
    return ADDON.getSetting('api_key')


def set_api_key(key):
    ADDON.setSetting('api_key', key)


def get_username():
    return ADDON.getSetting('username')


def set_username(name):
    ADDON.setSetting('username', name)


def api_request(method, path, data=None, timeout=30):
    headers = {'Content-Type': 'application/json'}
    key = get_api_key()
    if key:
        headers['X-API-Key'] = key
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f'{BASE_URL}{path}', data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def get_credits():
    try:
        return api_request('GET', '/api/kodi/credits', timeout=8)
    except Exception:
        return {'image_credits': 0, 'video_credits': 0}


def activate():
    device_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    activate_url = f'{BASE_URL}/kodi-activate?device={device_code}'
    dialog = xbmcgui.Dialog()
    progress = xbmcgui.DialogProgress()
    progress.create(
        'PercfectStudios — Connect your TV',
        'Scan the QR code with your phone OR go to:\n\n'
        f'  [B]{activate_url}[/B]\n\n'
        'Waiting for you to sign up on your phone...'
    )
    qr_image_url = f'https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(activate_url)}'
    xbmc.executebuiltin(f'ShowPicture({qr_image_url})')

    for i in range(200):
        if progress.iscanceled():
            progress.close()
            xbmc.executebuiltin('Action(Back)')
            return False
        time.sleep(3)
        progress.update(int((i / 200) * 100))
        try:
            data = api_request('GET', f'/api/kodi/poll/{device_code}', timeout=5)
            if data.get('status') == 'activated':
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
            if data.get('status') == 'expired':
                progress.close()
                dialog.ok('Code Expired', 'Your QR code expired. Please try again.')
                return False
        except Exception:
            pass

    progress.close()
    dialog.ok('Timed Out', 'Connection timed out. Please try again.')
    return False


def verify_pin():
    dialog = xbmcgui.Dialog()
    device_code = dialog.input('Enter device code (shown on phone after scanning)', type=xbmcgui.INPUT_ALPHANUM)
    if not device_code:
        return False
    pin = dialog.input('Enter your 6-digit PIN', type=xbmcgui.INPUT_NUMERIC)
    if not pin:
        return False
    try:
        data = api_request('POST', '/api/kodi/verify', {'device_code': device_code, 'pin': pin})
        set_api_key(data['api_key'])
        set_username(data.get('username', ''))
        dialog.ok('Connected!',
                  f'Welcome, [B]{data.get("username")}[/B]!\n\n'
                  f'[B]{data.get("image_credits", 0)}[/B] image credits\n'
                  f'[B]{data.get("video_credits", 0)}s[/B] video time')
        return True
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get('error', 'Authentication failed.')
        except Exception:
            err = 'Authentication failed.'
        dialog.ok('Error', err)
    except Exception as e:
        dialog.ok('Error', str(e))
    return False


def get_save_folder():
    folder = ADDON.getSetting('save_folder') or 'special://profile/addon_data/plugin.video.percfectstudios/generations/'
    path = xbmc.translatePath(folder)
    if not path.endswith(os.sep):
        path += os.sep
    return path


def should_auto_save():
    return ADDON.getSettingBool('auto_save')


def pick_image_file():
    path = xbmcgui.Dialog().browse(1, 'Select a photo', 'files', '', False, False, ['.jpg', '.jpeg', '.png', '.webp'])
    return path or None


def file_to_data_uri(path):
    mime = mimetypes.guess_type(path)[0] or 'image/jpeg'
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    return f'data:{mime};base64,{b64}'


def save_generation(url, kind, prompt):
    if not should_auto_save() or not url:
        return None
    folder = get_save_folder()
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        return None
    ext = 'jpg' if kind == 'image' else 'mp4'
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in (prompt or 'gen')[:24])
    dest = os.path.join(folder, f'{safe}_{int(time.time())}.{ext}')
    try:
        urllib.request.urlretrieve(url, dest)
        return dest
    except Exception:
        return None


def credits_header(credits):
    return f'[B]{credits.get("image_credits", 0)}[/B] images · [B]{credits.get("video_credits", 0)}s[/B] video'


def generate_image():
    credits = get_credits()
    if credits.get('image_credits', 0) <= 0:
        show_buy_credits('You have no image credits left.')
        return
    dialog = xbmcgui.Dialog()
    mode = dialog.select('Generate Image', ['Text prompt only', 'Use my photo (image-to-image)'])
    if mode < 0:
        return
    source_image = None
    if mode == 1:
        path = pick_image_file()
        if not path:
            return
        source_image = file_to_data_uri(path)
    prompt = dialog.input('Describe your image', type=xbmcgui.INPUT_ALPHANUM)
    if not prompt:
        return
    progress = xbmcgui.DialogProgress()
    progress.create('PercfectStudios', 'Generating your image...')
    progress.update(20)
    try:
        payload = {'prompt': prompt}
        if source_image:
            payload['image_url'] = source_image
        data = api_request('POST', '/api/generate/image', payload, timeout=90)
        progress.update(90)
        progress.close()
        url = data.get('url')
        if url:
            saved = save_generation(url, 'image', prompt)
            xbmc.executebuiltin(f'ShowPicture({url})')
            if saved:
                xbmcgui.Dialog().notification('PercfectStudios', f'Saved to {saved}', xbmcgui.NOTIFICATION_INFO, 4000)
    except urllib.error.HTTPError as e:
        progress.close()
        try:
            err = json.loads(e.read().decode()).get('error', 'Generation failed.')
        except Exception:
            err = 'Generation failed.'
        if e.code == 402 or 'credit' in err.lower():
            show_buy_credits(err)
        else:
            dialog.ok('Error', err)
    except Exception as e:
        progress.close()
        dialog.ok('Error', str(e))


def generate_video():
    credits = get_credits()
    vid_c = credits.get('video_credits', 0)
    if vid_c <= 0:
        show_buy_credits('You have no video seconds left.')
        return
    dialog = xbmcgui.Dialog()
    mode = dialog.select('Generate Video', ['Text prompt only', 'Animate my photo'])
    if mode < 0:
        return
    source_image = None
    if mode == 1:
        path = pick_image_file()
        if not path:
            return
        source_image = file_to_data_uri(path)
    durations = ['5 seconds', '6 seconds', '10 seconds', '15 seconds']
    dur_secs = [5, 6, 10, 15]
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
    for pct in range(10, 85, 3):
        if progress.iscanceled():
            progress.close()
            return
        time.sleep(2)
        progress.update(pct, f'Generating {duration}s video... {pct}%')
    try:
        payload = {'prompt': prompt, 'duration': duration}
        if source_image:
            payload['image_url'] = source_image
        data = api_request('POST', '/api/generate/video', payload, timeout=200)
        progress.update(95)
        progress.close()
        url = data.get('url')
        if url:
            saved = save_generation(url, 'video', prompt)
            li = xbmcgui.ListItem(prompt, path=url)
            li.setInfo('video', {'title': prompt})
            xbmc.Player().play(url, li)
            if saved:
                xbmcgui.Dialog().notification('PercfectStudios', f'Saved to {saved}', xbmcgui.NOTIFICATION_INFO, 4000)
    except urllib.error.HTTPError as e:
        progress.close()
        try:
            err = json.loads(e.read().decode()).get('error', 'Generation failed.')
        except Exception:
            err = 'Generation failed.'
        if e.code == 402 or 'credit' in err.lower():
            show_buy_credits(err)
        else:
            dialog.ok('Error', err)
    except Exception as e:
        progress.close()
        dialog.ok('Error', str(e))


def show_buy_credits(message=''):
    dialog = xbmcgui.Dialog()
    buy_url = f'{BASE_URL}/buy-credits'
    qr_url = f'https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={urllib.parse.quote(buy_url)}'
    choice = dialog.select('Get More Credits', [
        'Scan QR code with your phone (fastest)',
        'Type this URL: percfectai.com/buy-credits',
    ])
    if choice == 0:
        xbmc.executebuiltin(f'ShowPicture({qr_url})')
        dialog.ok('Scan to Buy Credits', f'{message}\n\nScan the QR code on screen with your phone.')
    elif choice == 1:
        dialog.ok('Buy Credits', f'{message}\n\nGo to:\n\n[B]percfectai.com/buy-credits[/B]')


def show_local_saves():
    folder = get_save_folder()
    xbmcplugin.setPluginCategory(HANDLE, 'Saved on this device')
    xbmcplugin.setContent(HANDLE, 'videos')
    try:
        names = sorted(os.listdir(folder), reverse=True)
    except Exception:
        xbmcgui.Dialog().ok('Saved Files', f'No saves yet.\n\nFolder:\n{folder}')
        return
    found = False
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        ext = name.rsplit('.', 1)[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'mp4'):
            continue
        found = True
        li = xbmcgui.ListItem(label=name)
        li.setPath(path)
        li.setProperty('IsPlayable', 'true')
        if ext in ('jpg', 'jpeg', 'png', 'webp'):
            li.setArt({'thumb': path})
            li.setInfo('image', {'title': name})
        else:
            li.setInfo('video', {'title': name, 'mediatype': 'video'})
        xbmcplugin.addDirectoryItem(HANDLE, path, li, False)
    if not found:
        xbmcgui.Dialog().ok('Saved Files', f'Nothing saved yet.\n\nFolder:\n{folder}')
        return
    xbmcplugin.endOfDirectory(HANDLE)


def show_gallery():
    try:
        items = api_request('GET', '/api/gallery', timeout=15)
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


def main_menu():
    api_key = get_api_key()
    if not api_key:
        dialog = xbmcgui.Dialog()
        choice = dialog.select('Welcome to PercfectStudios', [
            'Scan QR code to sign up / log in (recommended)',
            'Enter PIN manually (already scanned on phone)',
        ])
        if choice == 0 and activate():
            xbmc.executebuiltin('Container.Refresh')
        elif choice == 1 and verify_pin():
            xbmc.executebuiltin('Container.Refresh')
        return

    credits = get_credits()
    xbmcplugin.setPluginCategory(HANDLE, f'PercfectStudios — {credits_header(credits)}')
    xbmcplugin.setContent(HANDLE, 'videos')
    for label, action in [
        ('Generate Image', 'generate_image'),
        ('Generate Video', 'generate_video'),
        ('My Gallery (cloud)', 'gallery'),
        ('Saved on Device', 'local_saves'),
        ('Buy Credits', 'buy_credits'),
        ('Refresh Credits', 'refresh'),
        ('Settings', 'settings'),
        ('Disconnect', 'disconnect'),
    ]:
        li = xbmcgui.ListItem(label=label)
        li.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(HANDLE, f'plugin://{ADDON_ID}/?action={action}', li, True)
    xbmcplugin.endOfDirectory(HANDLE)


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
elif action == 'local_saves':
    show_local_saves()
elif action == 'buy_credits':
    show_buy_credits()
elif action == 'refresh':
    xbmc.executebuiltin('Container.Refresh')
    main_menu()
elif action == 'settings':
    ADDON.openSettings()
    xbmc.executebuiltin('Container.Refresh')
elif action == 'disconnect':
    if xbmcgui.Dialog().yesno('Disconnect', 'Remove your account from this device?'):
        set_api_key('')
        set_username('')
        xbmc.executebuiltin('Container.Refresh')
else:
    main_menu()
