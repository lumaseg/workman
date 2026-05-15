import Gio from 'gi://Gio';

const DBusInterface = `
<node>
  <interface name="org.workman.WindowManager">
    <method name="GetWindows">
      <arg type="s" direction="out" name="windows"/>
    </method>
    <method name="MoveWindow">
      <arg type="s" direction="in" name="wm_class"/>
      <arg type="i" direction="in" name="index"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="width"/>
      <arg type="i" direction="in" name="height"/>
      <arg type="b" direction="out" name="success"/>
    </method>
  </interface>
</node>`;

export default class WorkmanExtension {
    enable() {
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(DBusInterface, this);
        this._dbusImpl.export(Gio.DBus.session, '/org/workman/WindowManager');
        this._ownerId = Gio.DBus.session.own_name(
            'org.workman.WindowManager',
            Gio.BusNameOwnerFlags.NONE,
            null, null
        );
    }

    disable() {
        if (this._dbusImpl) {
            this._dbusImpl.unexport();
            this._dbusImpl = null;
        }
        if (this._ownerId) {
            Gio.DBus.session.unown_name(this._ownerId);
            this._ownerId = null;
        }
    }

    GetWindows() {
        const windows = global.get_window_actors().map(actor => {
            const win = actor.meta_window;
            const rect = win.get_frame_rect();
            return {
                title: win.get_title(),
                wm_class: win.get_wm_class(),
                pid: win.get_pid(),
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height
            };
        });
        return JSON.stringify(windows);
    }

    MoveWindow(wm_class, index, x, y, width, height) {
        const actors = global.get_window_actors();
        // Get all windows matching wm_class
        const matches = actors.filter(actor => 
            actor.meta_window.get_wm_class() === wm_class
        );
        
        if (index >= matches.length) {
            return false;
        }

        const win = matches[index].meta_window;
        win.move_resize_frame(false, x, y, width, height);
        return true;
    }
}
