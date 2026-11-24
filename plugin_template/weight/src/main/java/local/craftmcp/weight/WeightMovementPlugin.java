package local.craftmcp.weight;

import java.util.UUID;
import org.bukkit.Material;
import org.bukkit.attribute.Attribute;
import org.bukkit.attribute.AttributeInstance;
import org.bukkit.attribute.AttributeModifier;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.PlayerInventory;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitTask;

/** Applies a configurable movement-speed penalty for worn and held equipment. */
public final class WeightMovementPlugin extends JavaPlugin implements Listener {
    private static final UUID MODIFIER_ID = UUID.fromString("c6ed4e3e-aef5-4a3b-a9c2-b59d6ac1b2a3");
    private static final String MODIFIER_NAME = "craftmcp_equipment_weight";
    private BukkitTask updateTask;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        getServer().getPluginManager().registerEvents(this, this);
        long period = Math.max(1L, getConfig().getLong("update-period-ticks", 5L));
        updateTask = getServer().getScheduler().runTaskTimer(this, () -> {
            for (Player player : getServer().getOnlinePlayers()) updatePlayer(player);
        }, 1L, period);
        getLogger().info("WeightMovement enabled; equipment weight affects movement speed.");
    }

    private void updatePlayer(Player player) {
        PlayerInventory inventory = player.getInventory();
        double weight = 0.0;
        for (ItemStack armor : inventory.getArmorContents()) weight += itemWeight(armor);
        weight += itemWeight(inventory.getItemInMainHand());
        if (getConfig().getBoolean("count-offhand", false)) {
            weight += itemWeight(inventory.getItemInOffHand());
        }

        double loss = Math.max(0.0, getConfig().getDouble("speed-loss-per-weight", 0.025));
        double minimum = Math.max(0.05, Math.min(1.0,
                getConfig().getDouble("min-speed-multiplier", 0.35)));
        double baseSpeed = Math.max(minimum, getConfig().getDouble("base-speed-multiplier", 1.25));
        double multiplier = Math.max(minimum, baseSpeed - weight * loss);
        AttributeInstance attribute = player.getAttribute(Attribute.GENERIC_MOVEMENT_SPEED);
        if (attribute == null) return;

        AttributeModifier old = findModifier(attribute);
        double amount = multiplier - 1.0;
        if (old != null && Math.abs(old.getAmount() - amount) < 1.0E-6) return;
        if (old != null) attribute.removeModifier(old);
        if (Math.abs(amount) > 1.0E-6) {
            attribute.addModifier(new AttributeModifier(MODIFIER_ID, MODIFIER_NAME, amount,
                    AttributeModifier.Operation.MULTIPLY_SCALAR_1));
        }
    }

    private double itemWeight(ItemStack item) {
        if (item == null || item.getType() == Material.AIR) return 0.0;
        String key = item.getType().name();
        double perItem = Math.max(0.0, getConfig().getDouble("weights." + key, 0.0));
        return perItem * item.getAmount();
    }

    @EventHandler
    public void onPlayerQuit(PlayerQuitEvent event) {
        removeModifier(event.getPlayer());
    }

    private AttributeModifier findModifier(AttributeInstance attribute) {
        for (AttributeModifier modifier : attribute.getModifiers()) {
            if (MODIFIER_ID.equals(modifier.getUniqueId())) return modifier;
        }
        return null;
    }

    private void removeModifier(Player player) {
        AttributeInstance attribute = player.getAttribute(Attribute.GENERIC_MOVEMENT_SPEED);
        if (attribute == null) return;
        AttributeModifier modifier = findModifier(attribute);
        if (modifier != null) attribute.removeModifier(modifier);
    }

    @Override
    public void onDisable() {
        if (updateTask != null) updateTask.cancel();
        for (Player player : getServer().getOnlinePlayers()) {
            removeModifier(player);
        }
    }
}

