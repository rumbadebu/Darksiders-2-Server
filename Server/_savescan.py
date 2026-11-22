import pathlib, glob, subprocess, os
base = 'C:/Users/playerA/Documents/My Games/Darksiders2'
print('=== .dsav files ===')
for p in sorted(glob.glob(base + '/**/*.dsav', recursive=True)):
    b = pathlib.Path(p).read_bytes()
    print(p.split('Darksiders2')[-1], len(b), 'gloves=', b'armor_gloves_wanderer_d01' in b, 'axe=', b'weapon_axe_a01' in b, 'TriStone=', b.count(b'.TriStone'))
print('=== .7z backups (extract + scan) ===')
for p in sorted(glob.glob(base + '/*.7z')):
    try:
        out = subprocess.run(['C:\\Program Files\\7-Zip\\7z.exe', 'e', '-so', p], capture_output=True)
        data = out.stdout
        print(os.path.basename(p), 'extracted=', len(data), 'gloves=', b'armor_gloves_wanderer_d01' in data, 'axe=', b'weapon_axe_a01' in data, 'TriStone=', data.count(b'.TriStone'))
    except Exception as e:
        print(os.path.basename(p), 'ERR', e)
