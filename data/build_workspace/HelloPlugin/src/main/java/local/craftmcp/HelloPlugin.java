package local.craftmcp;

import org.bukkit.plugin.java.JavaPlugin;

public final class HelloPlugin extends JavaPlugin {
    @Override
    public void onEnable() {
        getLogger().info("HelloPlugin enabled");
    }
}
