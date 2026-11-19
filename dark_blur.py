#!/usr/bin/env python3
"""Configure the installed Blur My Shell extension for dark Kitty glass."""
import argparse
import datetime
import json
from pathlib import Path
import os

from gi.repository import Gio,GLib

UUID='blur-my-shell@aunetx'
PIPELINE='pipeline_kitty_dark_glass'


def settings_pair():
    paths=[Path.home()/'.local/share/gnome-shell/extensions'/UUID,
           Path('/usr/share/gnome-shell/extensions')/UUID]
    extension=next((p for p in paths if (p/'schemas/gschemas.compiled').exists()),None)
    if extension is None:
        raise RuntimeError('Не найдено установленное расширение Blur My Shell.')
    source=Gio.SettingsSchemaSource.new_from_directory(str(extension/'schemas'),Gio.SettingsSchemaSource.get_default(),False)
    settings=[]
    for name in ['org.gnome.shell.extensions.blur-my-shell','org.gnome.shell.extensions.blur-my-shell.applications']:
        schema=source.lookup(name,False)
        if schema is None:raise RuntimeError('Схема Blur My Shell не найдена: '+name)
        settings.append(Gio.Settings.new_full(schema,None,None))
    return settings


def with_dark_pipeline(value):
    # Retain every existing effect and its exact GVariant types.
    pipelines={}
    for i in range(value.n_children()):
        entry=value.get_child_value(i);name=entry.get_child_value(0).get_string()
        attributes=entry.get_child_value(1);result={}
        for j in range(attributes.n_children()):
            item=attributes.get_child_value(j)
            result[item.get_child_value(0).get_string()]=item.get_child_value(1).get_variant()
        pipelines[name]=result
    variant=GLib.Variant
    effect=variant('a{sv}',{
        'type':variant('s','dual_kawase_blur'),
        'id':variant('s','effect_kitty_dark_glass'),
        'params':variant('a{sv}',{'unscaled_radius':variant('i',32),'brightness':variant('d',.48)})})
    pipelines[PIPELINE]={'name':variant('s','Kitty Dark Glass'),'effects':variant('av',[effect])}
    return variant('a{sa{sv}}',pipelines)


def kitty_text(text):
    replacements={'background':'#000000','background_opacity':'0.68','dynamic_background_opacity':'yes',
                  'active_border_color':'#000000','inactive_border_color':'#000000'}
    lines=[];seen=set()
    for line in text.splitlines():
        parts=line.split();key=parts[0] if parts and not line.lstrip().startswith('#') else ''
        if key in replacements:
            if key not in seen:lines.append(key+' '+replacements[key]);seen.add(key)
        else:lines.append(line)
    lines.extend(k+' '+v for k,v in replacements.items() if k not in seen)
    return '\n'.join(lines)+'\n'


def configure(config_path,backup_path=None,dry_run=False):
    root,apps=settings_pair()
    pipeline=with_dark_pipeline(root.get_value('pipelines'))
    rules=apps.get_strv('whitelist')
    for app in ['arch-gruvbox-desktop','kitty']:
        if app not in rules:rules.append(app)
    options={'blur':GLib.Variant('b',True),'static-blur':GLib.Variant('b',True),
             'dynamic-opacity':GLib.Variant('b',False),'opacity':GLib.Variant('i',255),
             'pipeline-auto-mode':GLib.Variant('b',False),'pipeline':GLib.Variant('s',PIPELINE),
             'whitelist':GLib.Variant('as',rules)}
    if not config_path.is_file():raise RuntimeError('Не найден конфиг раскладки Kitty: '+str(config_path))
    new_config=kitty_text(config_path.read_text())
    if dry_run:
        print('Профиль проверен: radius=32, brightness=0.48, Kitty opacity=0.68, правила='+str(rules))
        return
    if not root.is_writable('pipelines') or not all(apps.is_writable(k) for k in options):
        raise RuntimeError('Настройки GNOME недоступны для записи. Запусти установщик из своей сессии GNOME.')
    backup_path=backup_path or config_path.parent/('blur-backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup_path.mkdir(parents=True,exist_ok=True)
    snapshot={'pipelines':root.get_value('pipelines').print_(True),
              'applications':{k:apps.get_value(k).print_(True) for k in options}}
    (backup_path/'blur-settings.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
    (backup_path/'kitty.conf.before-blur').write_text(config_path.read_text())
    if not root.set_value('pipelines',pipeline):raise RuntimeError('Не удалось сохранить профиль размытия.')
    apps.delay()
    for key,value in options.items():
        if not apps.set_value(key,value):
            apps.revert();raise RuntimeError('Не удалось записать настройку: '+key)
    apps.apply()
    shell=Gio.Settings.new('org.gnome.shell')
    enabled=shell.get_strv('enabled-extensions')
    if UUID not in enabled:
        enabled.append(UUID);shell.set_strv('enabled-extensions',enabled)
    Gio.Settings.sync()
    config_path.write_text(new_config)
    print('Тёмное размытие настроено для Kitty. Перезапусти Arch Gruvbox Desktop через меню приложений.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--kitty-config',type=Path,default=Path(__file__).parent/'kitty.conf')
    parser.add_argument('--backup-dir',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:configure(args.kitty_config,args.backup_dir,args.dry_run)
    except (OSError,RuntimeError) as error:parser.exit(1,str(error)+'\n')
