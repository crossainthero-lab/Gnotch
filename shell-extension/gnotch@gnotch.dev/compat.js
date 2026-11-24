// Shared by the shell extension and the preferences window (no Shell-only imports).
import Gio from 'gi://Gio';

export function loadCompat(dir) {
    try {
        const [, bytes] = Gio.File.new_for_path(`${dir}/compat.json`).load_contents(null);
        return JSON.parse(new TextDecoder().decode(bytes));
    } catch (e) {
        console.error(`gnotch: cannot read compat.json: ${e}`);
        return {replaced: {}, roles: [], extensions: {}};
    }
}
