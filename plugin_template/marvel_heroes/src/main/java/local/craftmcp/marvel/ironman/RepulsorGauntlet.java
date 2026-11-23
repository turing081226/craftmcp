package local.craftmcp.marvel.ironman;

import local.craftmcp.marvel.MarvelHeroesPlugin;
import org.bukkit.Location;
import org.bukkit.Sound;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.event.Event;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.Action;
import org.bukkit.event.entity.EntityDamageEvent;
import org.bukkit.event.player.PlayerInteractEntityEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.inventory.EquipmentSlot;
import org.bukkit.util.Vector;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Iron chestplate = repulsor gauntlet. Right-clicking with the repulsor (locked into hotbar slot 9 by
 * {@link SuitSlots}) selected fires a {@link RepulsorBolt}.
 */
public final class RepulsorGauntlet implements Listener {

    private final MarvelHeroesPlugin plugin;
    private final Map<UUID, Long> lastShot = new HashMap<>();

    /** Set while a repulsor explosion is being created, so the shooter is not hurt by their own blast. */
    private Entity explodingShooter;

    public RepulsorGauntlet(MarvelHeroesPlugin plugin) {
        this.plugin = plugin;
    }

    // ------------------------------------------------------------------ firing

    @EventHandler(priority = EventPriority.HIGH)
    public void onInteract(PlayerInteractEvent event) {
        if (event.getHand() != EquipmentSlot.HAND) return;
        if (!IronManSuit.isRepulsor(event.getItem())) return;
        // Never let the item do anything vanilla (e.g. place, use on blocks).
        event.setUseItemInHand(Event.Result.DENY);
        event.setUseInteractedBlock(Event.Result.DENY);
        Action a = event.getAction();
        if (a == Action.RIGHT_CLICK_AIR || a == Action.RIGHT_CLICK_BLOCK) tryFire(event.getPlayer());
    }

    @EventHandler(priority = EventPriority.HIGH)
    public void onInteractEntity(PlayerInteractEntityEvent event) {
        if (event.getHand() != EquipmentSlot.HAND) return;
        Player p = event.getPlayer();
        if (!IronManSuit.isRepulsor(p.getInventory().getItemInMainHand())) return;
        event.setCancelled(true); // no item frames / trades / leashing with the repulsor
        tryFire(p);
    }

    private void tryFire(Player p) {
        if (!IronManSuit.wearsChestplate(p)) return;
        if (p.getInventory().getHeldItemSlot() != IronManSuit.REPULSOR_SLOT) return;

        long now = System.currentTimeMillis();
        long cooldown = plugin.getConfig().getLong("repulsor.cooldown-ms", 350);
        Long last = lastShot.get(p.getUniqueId());
        if (last != null && now - last < cooldown) return;
        lastShot.put(p.getUniqueId(), now);

        Location eye = p.getEyeLocation();
        Vector dir = eye.getDirection().normalize();
        Vector right = dir.clone().crossProduct(new Vector(0, 1, 0));
        if (right.lengthSquared() > 1.0e-6) right.normalize().multiply(0.35);
        Location muzzle = eye.clone().add(dir.clone().multiply(0.7)).add(right).add(0, -0.3, 0);

        new RepulsorBolt(plugin, this, p, muzzle, dir).launch();
        p.swingMainHand();
    }

    void explode(Player shooter, Location at, float power, boolean fire, boolean breakBlocks) {
        explodingShooter = shooter;
        try {
            at.getWorld().createExplosion(at, power, fire, breakBlocks, shooter);
        } finally {
            explodingShooter = null;
        }
    }

    @EventHandler(ignoreCancelled = true)
    public void onSelfBlast(EntityDamageEvent event) {
        if (explodingShooter != null && event.getEntity() == explodingShooter
                && (event.getCause() == EntityDamageEvent.DamageCause.ENTITY_EXPLOSION
                || event.getCause() == EntityDamageEvent.DamageCause.BLOCK_EXPLOSION)) {
            event.setCancelled(true);
        }
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        lastShot.remove(event.getPlayer().getUniqueId());
    }
}
