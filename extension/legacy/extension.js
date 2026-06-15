const { Gio } = imports.gi;

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

let dbusImpl = null;
let ownerId = 0;

const service = {
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
    },

    MoveWindow(wm_class, index, x, y, width, height) {
        const actors = global.get_window_actors();
        const matches = actors.filter(actor =>
            actor.meta_window.get_wm_class() === wm_class
        );
        if (index >= matches.length) return false;
        matches[index].meta_window.move_resize_frame(false, x, y, width, height);
        return true;
    },

    CloseWindow(id) {
        // Identify by stable sequence so closing is unaffected by index shifts.
        const actor = global.get_window_actors().find(actor =>
            actor.meta_window.get_stable_sequence() === id
        );
        if (!actor) return false;
        // Graceful close — the app still gets to prompt for unsaved work.
        actor.meta_window.delete(global.get_current_time());
        return true;
    },
};

function init() {}

function enable() {
    dbusImpl = Gio.DBusExportedObject.wrapJSObject(DBusInterface, service);
    dbusImpl.export(Gio.DBus.session, '/org/workman/WindowManager');
    ownerId = Gio.DBus.session.own_name(
        'org.workman.WindowManager',
        Gio.BusNameOwnerFlags.NONE,
        null, null
    );
}

function disable() {
    if (dbusImpl) {
        dbusImpl.unexport();
        dbusImpl = null;
    }
    if (ownerId) {
        Gio.DBus.session.unown_name(ownerId);
        ownerId = 0;
    }
}
