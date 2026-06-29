import sys
import xbmc
import xbmcvfs
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

from prompt_presets import (
    PRINCESSES, OUTFITS, POSES, SCENES, EXPRESSIONS,
    build_prompt, random_prompt, pick_from_list,
)

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
BASE_URL = 'https://percfectai.com'
PERCHANCE_URL = 'https://perchance.org/percfect-imagine'
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 else 0
LAST_BATCH_FILE = 'last_batch.json'


def get_api_key():
    return ADDON.getSetting('api_key')


def set_api_key(key):
    ADDON.setSetting('api_key', key)


def set_username(name):
    ADDON.setSetting('username', name)


def get_batch_size():
    try:
        return max(1, min(10, int(ADDON.getSetting('batch_size') or '10')))
    except ValueError:
        return 10


def api_request(method, path, data=None, timeout=30):
    headers = {'Content-Type': 'application/json', 'X-Percfect-Client': 'kodi'}
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
        return {'video_credits': 0}


def get_addon_data_path():
    path = xbmcvfs.translatePath(f'special://profile/addon_data/{ADDON_ID}/')
    if not path.endswith(os.sep):
        path += os.sep
    return path


def get_save_folder():
    folder = ADDON.getSetting('save_folder') or f'special://profile/addon_data/{ADDON_ID}/My Percfect Pics/'
    path = xbmcvfs.translatePath(folder)
    if not path.endswith(os.sep):
        path += os.sep
    return path


def should_auto_save():
    return ADDON.getSettingBool('auto_save')


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


def save_last_batch(urls):
    try:
        with open(os.path.join(get_addon_data_path(), LAST_BATCH_FILE), 'w') as f:
            json.dump(urls, f)
    except Exception:
        pass


def load_last_batch():
    try:
        with open(os.path.join(get_addon_data_path(), LAST_BATCH_FILE)) as f:
            return json.load(f)
    except Exception:
        return []


def launch_url(url):
    try:
        xbmc.executebuiltin(f'StartAndroidActivity("", "{url}", "", "data")')
        return True
    except Exception:
        return False


def show_qr(url, title='Scan QR'):
    qr = f'https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={urllib.parse.quote(url)}'
    xbmc.executebuiltin(f'ShowPicture({qr})')
    xbmcgui.Dialog().ok(title, url)


def activate():
    device_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    activate_url = f'{BASE_URL}/kodi-activate?device={device_code}'
    progress = xbmcgui.DialogProgress()
    progress.create('PercfectStudios', 'Scan QR on your phone to connect…')
    show_qr(activate_url, 'Connect your TV')
    for i in range(200):
        if progress.iscanceled():
            progress.close()
            return False
        time.sleep(3)
        progress.update(int((i / 200) * 100))
        try:
            data = api_request('GET', f'/api/kodi/poll/{device_code}', timeout=5)
            if data.get('status') == 'activated':
                progress.close()
                set_api_key(data['api_key'])
                set_username(data.get('username', ''))
                xbmcgui.Dialog().ok('Connected!', f'Welcome [B]{data.get("username")}[/B]!\n[B]{data.get("video_credits", 0)}s[/B] video')
                return True
        except Exception as e:
            xbmc.log(f'PercfectStudios poll: {e}', xbmc.LOGERROR)
    progress.close()
    xbmcgui.Dialog().ok('Timed out', 'Try Settings → API Key or Enter PIN.')
    return False


def verify_pin():
    dialog = xbmcgui.Dialog()
    device_code = dialog.input('Device code from phone', type=xbmcgui.INPUT_ALPHANUM)
    if not device_code:
        return False
    pin = dialog.input('6-digit PIN', type=xbmcgui.INPUT_NUMERIC)
    if not pin:
        return False
    try:
        data = api_request('POST', '/api/kodi/verify', {'device_code': device_code, 'pin': pin})
        set_api_key(data['api_key'])
        set_username(data.get('username', ''))
        dialog.ok('Connected!', f'Welcome [B]{data.get("username")}[/B]!')
        return True
    except Exception as e:
        dialog.ok('Error', str(e))
    return False


def require_api_key():
    if get_api_key():
        return True
    choice = xbmcgui.Dialog().select('Connect account', ['Scan QR', 'Enter PIN'])
    if choice == 0:
        return activate()
    if choice == 1:
        return verify_pin()
    return False


def show_install_guide():
    choice = xbmcgui.Dialog().select('Install / Update', ['Show QR — percfectai.com/k', 'Show steps'])
    if choice == 0:
        show_qr(f'{BASE_URL}/k', 'Downloader URL')
        xbmcgui.Dialog().ok('Downloader', 'Type in Downloader app:\n\n  [B]percfectai.com/k[/B]')
    elif choice == 1:
        xbmcgui.Dialog().ok(
            'Fire Stick Install',
            '1. [B]Downloader[/B] → [B]percfectai.com/k[/B]\n'
            '2. [B]Kodi[/B] → Install from zip → Downloads\n'
            '3. [B]Install from repository[/B] → PercfectStudios\n'
            '4. Open add-on → Scan QR'
        )


# ── PERCHANCE section ─────────────────────────────────────

def perchance_open_generator():
    if launch_url(PERCHANCE_URL):
        xbmcgui.Dialog().notification('Percfect', 'Opening Perchance generator…', xbmcgui.NOTIFICATION_INFO, 3000)
    else:
        show_qr(PERCHANCE_URL, 'Perchance Generator')
        xbmcgui.Dialog().ok('Perchance', f'Open:\n\n[B]{PERCHANCE_URL}[/B]\n\nGenerate 10 → copy image URL → Save Image')


def perchance_phone_keyboard():
    device_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    show_qr(f'{BASE_URL}/connect?device={device_code}', 'Phone Keyboard')


def perchance_save_url():
    url = xbmcgui.Dialog().input('Paste image URL from Perchance', type=xbmcgui.INPUT_ALPHANUM)
    if not url or not url.startswith('http'):
        return
    saved = save_generation(url.strip(), 'image', 'perchance')
    if saved:
        xbmc.executebuiltin(f'ShowPicture({saved})')
        xbmcgui.Dialog().notification('Percfect', 'Saved to My Percfect Pics', xbmcgui.NOTIFICATION_INFO, 4000)
    else:
        xbmcgui.Dialog().ok('Error', 'Could not download that URL.')


def perchance_help():
    xbmcgui.Dialog().ok(
        'Perchance — Free',
        '1. Open Generator\n'
        '2. Pick princess + outfit → Generate 10\n'
        '3. Right-click image → copy address\n'
        '4. Save Image URL → My Percfect Pics\n\n'
        'No credits needed.'
    )


def show_perchance_menu():
    xbmcplugin.setPluginCategory(HANDLE, 'PERCHANCE — free images')
    xbmcplugin.setContent(HANDLE, 'videos')
    for label, action in [
        ('Open Generator', 'pc_open'),
        ('Phone Keyboard (QR)', 'pc_phone'),
        ('Save Image URL', 'pc_save'),
        ('How it works', 'pc_help'),
    ]:
        li = xbmcgui.ListItem(label)
        xbmcplugin.addDirectoryItem(HANDLE, f'plugin://{ADDON_ID}/?action={action}', li, True)
    xbmcplugin.endOfDirectory(HANDLE)


# ── FAL section ───────────────────────────────────────────

def fal_generate_with_presets():
    if not require_api_key():
        return
    princess = pick_from_list('Princess', PRINCESSES)
    if princess is None:
        return
    outfit = pick_from_list('Outfit', OUTFITS)
    if outfit is None:
        return
    pose = pick_from_list('Pose', POSES)
    if pose is None:
        return
    scene = pick_from_list('Scene', SCENES)
    if scene is None:
        return
    expression = pick_from_list('Expression', EXPRESSIONS)
    if expression is None:
        return
    extra = xbmcgui.Dialog().input('Extra details (optional)', type=xbmcgui.INPUT_ALPHANUM) or ''
    prompt = build_prompt(princess, outfit, pose, scene, expression, extra)
    fal_run_generation(prompt)


def fal_surprise_me():
    if not require_api_key():
        return
    fal_run_generation(random_prompt())


def fal_custom_prompt():
    if not require_api_key():
        return
    prompt = xbmcgui.Dialog().input('Describe your image', type=xbmcgui.INPUT_ALPHANUM)
    if not prompt:
        return
    fal_run_generation(prompt)


def fal_run_generation(prompt):
    n = get_batch_size()
    progress = xbmcgui.DialogProgress()
    progress.create('FAL — FLUX Schnell', f'Generating {n} images… ~30–60s')
    try:
        for pct in range(5, 85, 5):
            if progress.iscanceled():
                progress.close()
                return
            time.sleep(1)
            progress.update(pct)
        data = api_request('POST', '/api/generate/image', {'prompt': prompt, 'num_images': n}, timeout=180)
        progress.close()
        urls = data.get('urls') or ([data['url']] if data.get('url') else [])
        if not urls:
            xbmcgui.Dialog().ok('Error', 'No images returned.')
            return
        save_last_batch(urls)
        for url in urls:
            save_generation(url, 'image', prompt[:20])
        show_batch_gallery(urls, prompt)
    except urllib.error.HTTPError as e:
        progress.close()
        try:
            err = json.loads(e.read().decode()).get('error', 'Generation failed.')
        except Exception:
            err = 'Generation failed.'
        xbmcgui.Dialog().ok('Error', err)
    except Exception as e:
        progress.close()
        xbmcgui.Dialog().ok('Error', str(e))


def show_batch_gallery(urls, prompt=''):
    xbmcplugin.setPluginCategory(HANDLE, f'Batch — {len(urls)} images')
    xbmcplugin.setContent(HANDLE, 'images')
    for i, url in enumerate(urls):
        li = xbmcgui.ListItem(label=f'Image {i + 1}')
        li.setArt({'thumb': url, 'poster': url})
        li.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(
            HANDLE,
            f'plugin://{ADDON_ID}/?action=view_image&url={urllib.parse.quote(url, safe="")}&prompt={urllib.parse.quote(prompt, safe="")}',
            li, True)
    xbmcplugin.endOfDirectory(HANDLE)


def view_image(url, prompt):
    choice = xbmcgui.Dialog().select('Image', ['View fullscreen', 'Animate to video (480p)'])
    if choice == 0:
        xbmc.executebuiltin(f'ShowPicture({url})')
    elif choice == 1:
        animate_image_url(url)


def fal_view_last_batch():
    urls = load_last_batch()
    if not urls:
        xbmcgui.Dialog().ok('No batch', 'Generate images first.')
        return
    show_batch_gallery(urls)


def show_fal_menu():
    xbmcplugin.setPluginCategory(HANDLE, 'FAL — FLUX Schnell batch')
    xbmcplugin.setContent(HANDLE, 'videos')
    for label, action in [
        ('Easy Generate (pick presets)', 'fal_easy'),
        ('Surprise Me', 'fal_surprise'),
        ('Custom Prompt', 'fal_custom'),
        ('View Last Batch', 'fal_last'),
    ]:
        li = xbmcgui.ListItem(label)
        xbmcplugin.addDirectoryItem(HANDLE, f'plugin://{ADDON_ID}/?action={action}', li, True)
    xbmcplugin.endOfDirectory(HANDLE)


# ── SHARED section ────────────────────────────────────────

def file_to_data_uri(path):
    mime = mimetypes.guess_type(path)[0] or 'image/jpeg'
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    return f'data:{mime};base64,{b64}'


def pick_image_file():
    return xbmcgui.Dialog().browse(1, 'Select a photo', 'files', '', False, False, ['.jpg', '.jpeg', '.png', '.webp']) or None


def animate_image_url(image_url):
    if not require_api_key():
        return
    credits = get_credits()
    vid_c = credits.get('video_credits', 0)
    if vid_c <= 0:
        show_buy_credits('No video seconds left.')
        return
    motions = ['Slow cinematic zoom in', 'Hair blowing in wind', 'Gentle body movement', 'Custom…']
    idx = xbmcgui.Dialog().select('Motion', motions)
    if idx < 0:
        return
    if idx == 3:
        prompt = xbmcgui.Dialog().input('Describe motion', type=xbmcgui.INPUT_ALPHANUM)
    else:
        prompt = motions[idx]
    if not prompt:
        return
    durations = ['5 sec (480p)', '6 sec (480p)', '10 sec (480p)']
    dur_secs = [5, 6, 10]
    d_idx = xbmcgui.Dialog().select(f'Length ({vid_c}s left)', durations)
    if d_idx < 0:
        return
    duration = dur_secs[d_idx]
    if vid_c < duration:
        show_buy_credits(f'Need {duration}s, have {vid_c}s.')
        return
    progress = xbmcgui.DialogProgress()
    progress.create('Video', f'Generating {duration}s 480p video…')
    try:
        payload = {'prompt': prompt, 'duration': duration, 'resolution': '480p', 'image_url': image_url}
        data = api_request('POST', '/api/generate/video', payload, timeout=200)
        progress.close()
        url = data.get('url')
        if url:
            save_generation(url, 'video', prompt)
            li = xbmcgui.ListItem(prompt, path=url)
            li.setInfo('video', {'title': prompt})
            xbmc.Player().play(url, li)
    except Exception as e:
        progress.close()
        xbmcgui.Dialog().ok('Error', str(e))


def generate_video():
    if not require_api_key():
        return
    credits = get_credits()
    vid_c = credits.get('video_credits', 0)
    if vid_c <= 0:
        show_buy_credits('No video seconds left.')
        return
    mode = xbmcgui.Dialog().select('Animate Video (480p)', ['From My Percfect Pics', 'From photo file', 'Text only'])
    if mode < 0:
        return
    source_image = None
    if mode == 0:
        path = pick_image_file()
        if path:
            source_image = file_to_data_uri(path)
    elif mode == 1:
        path = pick_image_file()
        if path:
            source_image = file_to_data_uri(path)
    motions = ['Slow zoom', 'Hair blowing', 'Cinematic pan', 'Custom']
    m_idx = xbmcgui.Dialog().select('Motion', motions)
    if m_idx < 0:
        return
    prompt = motions[m_idx] if m_idx < 3 else xbmcgui.Dialog().input('Describe motion', type=xbmcgui.INPUT_ALPHANUM)
    if not prompt:
        return
    durations = ['5 sec', '6 sec', '10 sec']
    dur_secs = [5, 6, 10]
    d_idx = xbmcgui.Dialog().select(f'Length ({vid_c}s)', durations)
    if d_idx < 0:
        return
    duration = dur_secs[d_idx]
    progress = xbmcgui.DialogProgress()
    progress.create('Video', f'Generating {duration}s…')
    try:
        payload = {'prompt': prompt, 'duration': duration, 'resolution': '480p'}
        if source_image:
            payload['image_url'] = source_image
        data = api_request('POST', '/api/generate/video', payload, timeout=200)
        progress.close()
        url = data.get('url')
        if url:
            save_generation(url, 'video', prompt)
            xbmc.Player().play(url, xbmcgui.ListItem(prompt, path=url))
    except Exception as e:
        progress.close()
        xbmcgui.Dialog().ok('Error', str(e))


def show_local_saves():
    folder = get_save_folder()
    xbmcplugin.setPluginCategory(HANDLE, 'My Percfect Pics')
    xbmcplugin.setContent(HANDLE, 'images')
    try:
        names = sorted(
            [n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))],
            reverse=True,
        )
    except Exception:
        xbmcgui.Dialog().ok('My Percfect Pics', 'No pics yet. Generate in Perchance or FAL.')
        return
    found = False
    for name in names:
        ext = name.rsplit('.', 1)[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'mp4'):
            continue
        found = True
        path = os.path.join(folder, name)
        li = xbmcgui.ListItem(label=name)
        li.setArt({'thumb': path, 'poster': path})
        if ext == 'mp4':
            li.setProperty('IsPlayable', 'true')
            li.setPath(path)
            li.setInfo('video', {'title': name})
            xbmcplugin.addDirectoryItem(HANDLE, path, li, False)
        else:
            li.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(
                HANDLE,
                f'plugin://{ADDON_ID}/?action=view_local&path={urllib.parse.quote(path, safe="")}',
                li, True)
    if not found:
        xbmcgui.Dialog().ok('My Percfect Pics', 'No pics yet.')
        return
    xbmcplugin.endOfDirectory(HANDLE)


def view_local(path):
    choice = xbmcgui.Dialog().select(path.split('/')[-1], ['View', 'Animate to video'])
    if choice == 0:
        xbmc.executebuiltin(f'ShowPicture({path})')
    elif choice == 1:
        animate_image_url(file_to_data_uri(path))


def show_account():
    credits = get_credits()
    user = ADDON.getSetting('username') or 'Connected'
    choice = xbmcgui.Dialog().select(
        f'{user} — {credits.get("video_credits", 0)}s video',
        ['Scan QR reconnect', 'Buy video credits', 'Disconnect'],
    )
    if choice == 0:
        activate()
    elif choice == 1:
        show_buy_credits()
    elif choice == 2:
        if xbmcgui.Dialog().yesno('Disconnect', 'Remove account from this device?'):
            set_api_key('')
            set_username('')
            xbmc.executebuiltin('Container.Refresh')


def show_buy_credits(message=''):
    show_qr(f'{BASE_URL}/buy-credits', 'Buy Credits')
    xbmcgui.Dialog().ok('Buy Credits', f'{message}\n\n[B]percfectai.com/buy-credits[/B]')


def show_shared_menu():
    xbmcplugin.setPluginCategory(HANDLE, 'SHARED')
    xbmcplugin.setContent(HANDLE, 'videos')
    for label, action in [
        ('My Percfect Pics', 'sh_pics'),
        ('Animate Video (480p)', 'sh_video'),
        ('Account & Credits', 'sh_account'),
        ('How to Install', 'sh_install'),
        ('Settings', 'sh_settings'),
    ]:
        li = xbmcgui.ListItem(label)
        xbmcplugin.addDirectoryItem(HANDLE, f'plugin://{ADDON_ID}/?action={action}', li, True)
    xbmcplugin.endOfDirectory(HANDLE)


# ── Home ──────────────────────────────────────────────────

def main_menu():
    credits = get_credits() if get_api_key() else {}
    vid = credits.get('video_credits', 0)
    xbmcplugin.setPluginCategory(HANDLE, f'PercfectStudios — {vid}s video')
    xbmcplugin.setContent(HANDLE, 'videos')
    sections = [
        ('PERCHANCE — free images', 'home_perchance'),
        ('FAL — fast batch (FLUX Schnell)', 'home_fal'),
        ('SHARED — pics, video, account', 'home_shared'),
    ]
    if not get_api_key():
        sections.append(('Connect account (QR)', 'home_connect'))
    for label, action in sections:
        li = xbmcgui.ListItem(label)
        li.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(HANDLE, f'plugin://{ADDON_ID}/?action={action}', li, True)
    xbmcplugin.endOfDirectory(HANDLE)


# ── Router ────────────────────────────────────────────────

params = {}
if len(sys.argv) > 2 and sys.argv[2]:
    params = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?')))

action = params.get('action', '')

ROUTES = {
    'home_perchance': show_perchance_menu,
    'home_fal': show_fal_menu,
    'home_shared': show_shared_menu,
    'home_connect': activate,
    'pc_open': perchance_open_generator,
    'pc_phone': perchance_phone_keyboard,
    'pc_save': perchance_save_url,
    'pc_help': perchance_help,
    'fal_easy': fal_generate_with_presets,
    'fal_surprise': fal_surprise_me,
    'fal_custom': fal_custom_prompt,
    'fal_last': fal_view_last_batch,
    'sh_pics': show_local_saves,
    'sh_video': generate_video,
    'sh_account': show_account,
    'sh_install': show_install_guide,
    'sh_settings': lambda: ADDON.openSettings(),
}

if action == 'view_image':
    view_image(urllib.parse.unquote(params.get('url', '')), urllib.parse.unquote(params.get('prompt', '')))
elif action == 'view_local':
    view_local(urllib.parse.unquote(params.get('path', '')))
elif action in ROUTES:
    ROUTES[action]()
else:
    main_menu()