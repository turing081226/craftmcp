package local.craftmcp.blazefireball;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import org.bukkit.Color;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.Particle;
import org.bukkit.Sound;
import org.bukkit.World;
import org.bukkit.entity.LargeFireball;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.block.Action;
import org.bukkit.event.entity.ProjectileHitEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.inventory.EquipmentSlot;
import org.bukkit.inventory.ItemStack;
import org.bukkit.metadata.FixedMetadataValue;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitRunnable;
import org.bukkit.util.Vector;

public final class BlazeFireballPlugin extends JavaPlugin implements Listener {
    private static final String META = "blazefireball";
    private static final long COOLDOWN_MS = 800L;
    private static final double SPEED = 1.6;
    private final Map<UUID, Long> lastCast = new HashMap<>();

    @Override
    public void onEnable() {
        getServer().getPluginManager().registerEvents(this, this);
        getLogger().info("BlazeFireball enabled: right-click with a blaze rod!");
    }

    @EventHandler
    public void onInteract(PlayerInteractEvent event) {
        if (event.getHand() != EquipmentSlot.HAND) return;
        Action action = event.getAction();
        if (action != Action.RIGHT_CLICK_AIR && action != Action.RIGHT_CLICK_BLOCK) return;
        ItemStack item = event.getItem();
        if (item == null || item.getType() != Material.BLAZE_ROD) return;
        Player player = event.getPlayer();
        if (!player.hasPermission("blazefireball.use")) return;
        event.setCancelled(true);

        long now = System.currentTimeMillis();
        Long last = lastCast.get(player.getUniqueId());
        if (last != null && now - last < COOLDOWN_MS) return;
        lastCast.put(player.getUniqueId(), now);
        cast(player);
    }

    private void cast(Player player) {
        World world = player.getWorld();
        Location eye = player.getEyeLocation();
        Vector dir = eye.getDirection().normalize();
        Location spawn = eye.clone().add(dir.clone().multiply(1.5));

        // Casting burst: flame ring around the player and a muzzle flash
        castRing(player);
        world.spawnParticle(Particle.FLASH, spawn, 1, 0, 0, 0, 0);
        world.spawnParticle(Particle.LAVA, spawn, 8, 0.2, 0.2, 0.2, 0);
        world.playSound(eye, Sound.ENTITY_BLAZE_SHOOT, 1.2f, 0.8f);
        world.playSound(eye, Sound.ENTITY_GHAST_SHOOT, 0.8f, 1.2f);
        world.playSound(eye, Sound.ITEM_FIRECHARGE_USE, 1.0f, 0.6f);

        LargeFireball fireball = world.spawn(spawn, LargeFireball.class, fb -> {
            fb.setShooter(player);
            fb.setDirection(dir);
            fb.setVelocity(dir.clone().multiply(SPEED));
            fb.setYield(2.5f);
            fb.setIsIncendiary(true);
            fb.setMetadata(META, new FixedMetadataValue(this, true));
        });

        // Spiralling flame trail
        new BukkitRunnable() {
            int tick = 0;
            @Override
            public void run() {
                if (!fireball.isValid() || tick++ > 200) {
                    cancel();
                    return;
                }
                Location loc = fireball.getLocation();
                Vector v = fireball.getVelocity();
                if (v.lengthSquared() < 1.0E-4) v = dir;
                Vector fwd = v.clone().normalize();
                Vector side = fwd.clone().crossProduct(new Vector(0, 1, 0));
                if (side.lengthSquared() < 1.0E-4) side = new Vector(1, 0, 0);
                side.normalize();
                Vector up = side.clone().crossProduct(fwd).normalize();
                for (int arm = 0; arm < 2; arm++) {
                    double angle = tick * 0.6 + arm * Math.PI;
                    Vector off = side.clone().multiply(Math.cos(angle) * 0.6).add(up.clone().multiply(Math.sin(angle) * 0.6));
                    world.spawnParticle(Particle.FLAME, loc.clone().add(off), 2, 0.02, 0.02, 0.02, 0.005);
                }
                world.spawnParticle(Particle.SMALL_FLAME, loc, 6, 0.25, 0.25, 0.25, 0.01);
                world.spawnParticle(Particle.SMOKE_LARGE, loc, 1, 0.1, 0.1, 0.1, 0.01);
                world.spawnParticle(Particle.REDSTONE, loc, 4, 0.3, 0.3, 0.3, 0,
                        new Particle.DustOptions(Color.fromRGB(255, 120 + (tick * 7) % 100, 0), 1.6f));
            }
        }.runTaskTimer(this, 0L, 1L);
    }

    private void castRing(Player player) {
        Location base = player.getLocation().add(0, 0.1, 0);
        World world = player.getWorld();
        new BukkitRunnable() {
            double radius = 0.4;
            @Override
            public void run() {
                if (radius > 2.4) {
                    cancel();
                    return;
                }
                for (int i = 0; i < 24; i++) {
                    double a = 2 * Math.PI * i / 24;
                    Location p = base.clone().add(Math.cos(a) * radius, 0, Math.sin(a) * radius);
                    world.spawnParticle(Particle.FLAME, p, 1, 0, 0.05, 0, 0.01);
                }
                radius += 0.5;
            }
        }.runTaskTimer(this, 0L, 1L);
    }

    @EventHandler
    public void onHit(ProjectileHitEvent event) {
        if (!(event.getEntity() instanceof LargeFireball fb) || !fb.hasMetadata(META)) return;
        Location loc = fb.getLocation();
        World world = loc.getWorld();
        world.spawnParticle(Particle.EXPLOSION_HUGE, loc, 1);
        world.spawnParticle(Particle.LAVA, loc, 30, 1.0, 0.6, 1.0, 0);
        world.spawnParticle(Particle.FLAME, loc, 120, 0.4, 0.4, 0.4, 0.25);
        world.spawnParticle(Particle.CAMPFIRE_COSY_SMOKE, loc, 15, 0.8, 0.5, 0.8, 0.03);
        world.playSound(loc, Sound.ENTITY_GENERIC_EXPLODE, 1.5f, 0.7f);
        world.playSound(loc, Sound.ENTITY_BLAZE_DEATH, 0.8f, 0.6f);
    }

    @Override
    public void onDisable() {
        lastCast.clear();
    }
}
