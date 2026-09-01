import Gio from 'gi://Gio';

// Find window actors matching a saved wm_class, tolerating differences in how
// the same app is packaged across machines (e.g. a session saved against the
// Flatpak Firefox's "org.mozilla.firefox" being restored onto a native
// Firefox whose class is just "firefox"). Exact matches always win; looser
// tiers are only consulted when the exact class isn't present, so a window
// with the real class is never mis-routed.
function matchWindows(actors, wmClass) {
    const classOf = a => a.meta_window.get_wm_class() || '';

    let matches = actors.filter(a => classOf(a) === wmClass);
    if (matches.length) return matches;

    const want = wmClass.toLowerCase();
    matches = actors.filter(a => classOf(a).toLowerCase() === want);
    if (matches.length) return matches;

    // Compare the final dotted segment (org.mozilla.firefox -> firefox) and
    // allow substring overlap either way.
    const lastSegment = s => s.toLowerCase().split('.').pop();
    const wantSeg = lastSegment(wmClass);
    return actors.filter(a => {
        const c = classOf(a).toLowerCase();
        if (!c) return false;
        return lastSegment(c) === wantSeg || c.includes(want) || want.includes(c);
    });
}

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
    <method name="CloseWindow">
      <arg type="u" direction="in" name="id"/>
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
                id: win.get_stable_sequence(),
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
        const matches = matchWindows(global.get_window_actors(), wm_class);

        if (index >= matches.length) {
            return false;
        }

        const win = matches[index].meta_window;
        win.move_resize_frame(false, x, y, width, height);
        return true;
    }

    CloseWindow(id) {
        // Identify the window by its stable sequence (unique for its lifetime)
        // so closing is unaffected by index shifts as other windows close.
        const actor = global.get_window_actors().find(actor =>
            actor.meta_window.get_stable_sequence() === id
        );
        if (!actor) {
            return false;
        }
        // Graceful close — the app still gets to prompt for unsaved work.
        actor.meta_window.delete(global.get_current_time());
        return true;
    }
}
