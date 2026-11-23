package local.craftmcp.marvel.spiderman;

import local.craftmcp.marvel.MarvelHeroesPlugin;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.event.entity.EntityDamageEvent.DamageCause;

/** Melee hits (including sweeps) from a player in the suit deal {@code spiderman.attack-multiplier}x damage. */
public final class SpiderStrength implements Listener {

    private final MarvelHeroesPlugin plugin;

    public SpiderStrength(MarvelHeroesPlugin plugin) {
        this.plugin = plugin;
    }

    @EventHandler(priority = EventPriority.HIGH, ignoreCancelled = true)
    public void onHit(EntityDamageByEntityEvent event) {
        if (!(event.getDamager() instanceof Player p) || !SpiderSuit.wearsSuit(p)) return;
        if (event.getCause() != DamageCause.ENTITY_ATTACK && event.getCause() != DamageCause.ENTITY_SWEEP_ATTACK) return;
        double multiplier = plugin.getConfig().getDouble("spiderman.attack-multiplier", 4.0);
        double before = event.getDamage();
        event.setDamage(before * multiplier);
        plugin.debug(String.format("[spider] %s hit %s: %.2f -> %.2f", p.getName(),
                event.getEntity().getType(), before, event.getDamage()));
    }
}
