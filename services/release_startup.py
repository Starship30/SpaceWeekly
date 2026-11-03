"""Framework hooks run before Qt, workspace initialization or production subprocesses."""
import sys


def install_uninstaller(*_args):
    from pathlib import Path
    import shutil,logging
    current=Path(sys.executable).resolve().parent
    if current.name!='current' or not (current.parent/'Update.exe').is_file():return
    try:shutil.copy2(current/'Uninstall.exe',current.parent/'Uninstall.exe')
    except OSError:logging.getLogger(__name__).exception('Unable to install standalone uninstall entry')


def early_startup():
    if len(sys.argv)==3 and sys.argv[1]=='--spaceweekly-update-worker':
        from tools.update_worker import run
        run(sys.argv[2]);return True
    if getattr(sys,'frozen',False) and sys.platform=='win32':
        import velopack
        (velopack.App().set_auto_apply_on_startup(False)
            .on_after_install_fast_callback(install_uninstaller)
            .on_after_update_fast_callback(install_uninstaller).run())
    return False
