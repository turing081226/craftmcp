package io.github.craftmcp.debuggersample;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.command.RemoteConsoleCommandSender;
import org.bukkit.plugin.Plugin;

/** Exact read-only plugin diagnostics for the isolated integration harness. */
public final class PluginStatusCommand implements CommandExecutor {
    private final Plugin owner;

    public PluginStatusCommand(Plugin owner) {
        this.owner = owner;
    }

    private static String encode(String value) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!(sender instanceof RemoteConsoleCommandSender)) {
            sender.sendMessage("CRAFTMCP_PLUGIN_STATUS v=1 error=REMOTE_CONSOLE_REQUIRED\n");
            return true;
        }
        if (args.length != 1 || !args[0].matches("[A-Za-z0-9_-]{1,64}")) {
            sender.sendMessage("CRAFTMCP_PLUGIN_STATUS v=1 error=INVALID_ARGS\n");
            return true;
        }
        Plugin plugin = owner.getServer().getPluginManager().getPlugin(args[0]);
        if (plugin == null || !plugin.getName().equals(args[0])) {
            sender.sendMessage("CRAFTMCP_PLUGIN_STATUS v=1 missing=" + encode(args[0]) + "\n");
            return true;
        }
        sender.sendMessage("CRAFTMCP_PLUGIN_STATUS v=1 name=" + encode(plugin.getName())
                + " version=" + encode(plugin.getDescription().getVersion())
                + " enabled=" + plugin.isEnabled() + "\n");
        return true;
    }
}
