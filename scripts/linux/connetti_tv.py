#!/usr/bin/env python3
# Collega K16 al Fire Stick (Miracast/WFD).
# Prerequisito: sul Fire Stick, Duplicazione schermo attiva/in attesa.
# Flusso in due fasi: (A) prepara e aspetta il dispositivo, poi chiede
# all'utente un SOLO click manuale via RDP sulla riga del dispositivo
# (il click automatico via script non e' affidabile su Wayland);
# (B) rileva il dialogo di condivisione successivo al click e lo
# completa da solo.
# Uso: python3 connetti_tv.py
import pyatspi
import subprocess
import time
import sys
import os

os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/1000/bus')

DEVICE_NAME = 'FireTVStick'
SCREEN_NAME = 'Pantalla virtual'


def get_app(name):
    desktop = pyatspi.Registry.getDesktop(0)
    for i in range(desktop.childCount):
        a = desktop.getChildAtIndex(i)
        try:
            if a.name == name:
                return a
        except Exception:
            pass
    return None


def find(acc, target_name, target_role, depth=0, maxdepth=20):
    try:
        if acc.name == target_name and acc.getRoleName() == target_role:
            return acc
    except Exception:
        pass
    if depth >= maxdepth:
        return None
    try:
        for i in range(acc.childCount):
            r = find(acc.getChildAtIndex(i), target_name, target_role, depth + 1, maxdepth)
            if r:
                return r
    except Exception:
        pass
    return None


def find_listbox(acc):
    try:
        if acc.getRoleName() == 'list':
            return acc
        for i in range(acc.childCount):
            r = find_listbox(acc.getChildAtIndex(i))
            if r:
                return r
    except Exception:
        pass
    return None


def click_action(acc):
    act = acc.queryAction()
    for i in range(act.nActions):
        act.doAction(i)


# FASE A: prepara la app e aspetta che il dispositivo compaia in lista
subprocess.run(['pkill', '-9', '-f', 'gnome-network-displays'], stderr=subprocess.DEVNULL)
time.sleep(1)
subprocess.Popen(['gnome-network-displays'], stdout=open('/tmp/gnd_auto.log', 'w'), stderr=subprocess.STDOUT)
time.sleep(3)

gnd = get_app('gnome-network-displays')
if not gnd:
    print('ERRORE: gnome-network-displays non si avvia')
    sys.exit(1)

item = None
for _ in range(30):
    frame = gnd.getChildAtIndex(0)
    listbox = find_listbox(frame)
    if listbox:
        for i in range(listbox.childCount):
            c = listbox.getChildAtIndex(i)
            if c.name == DEVICE_NAME:
                item = c
                break
    if item:
        break
    time.sleep(1)

if not item:
    print(f'ERRORE: {DEVICE_NAME} non trovato dopo 30s. Assicurati che la Duplicazione schermo sia attiva sul Fire Stick e riprova.')
    sys.exit(1)

print(f'{DEVICE_NAME} trovato ed e\' pronto in lista.')
print('=> Apri ORA la connessione RDP dal PC (192.168.8.130, utente eddy) e fai UN CLICK sulla riga "FireTVStick".')
print('In attesa del tuo click (fino a 90s)...')

# FASE B: aspetta che l'utente clicchi manualmente via RDP, poi rileva
# il dialogo di condivisione e lo completa da solo.
picker = None
for _ in range(90):
    portal = get_app('xdg-desktop-portal-gnome')
    if portal:
        toggle = find(portal, SCREEN_NAME, 'toggle button')
        share = find(portal, 'Compartir', 'button')
        if toggle and share:
            picker = (toggle, share)
            break
    # se non appare il dialogo ma lo streaming e' gia' partito (permesso gia' concesso in passato)
    gnd_now = get_app('gnome-network-displays')
    if gnd_now and find(gnd_now, 'Emitir', 'label'):
        print('CONNESSO: streaming verso', DEVICE_NAME, 'gia\' attivo (stato Emitir).')
        sys.exit(0)
    time.sleep(1)

if not picker:
    print('Timeout: nessun click rilevato entro 90s. Rilancia lo script quando sei pronto a cliccare.')
    sys.exit(1)

toggle, share = picker
print(f'Dialogo di condivisione rilevato, seleziono {SCREEN_NAME} e confermo...')
click_action(toggle)
time.sleep(1)
click_action(share)

# verifica stato finale (fino a 10s)
for _ in range(10):
    gnd = get_app('gnome-network-displays')
    emit = find(gnd, 'Emitir', 'label') if gnd else None
    if emit:
        print('CONNESSO: streaming verso', DEVICE_NAME, 'attivo (stato Emitir).')
        sys.exit(0)
    time.sleep(1)

print('Non confermato lo stato di streaming attivo entro il timeout. Verifica manualmente via RDP.')
sys.exit(2)
