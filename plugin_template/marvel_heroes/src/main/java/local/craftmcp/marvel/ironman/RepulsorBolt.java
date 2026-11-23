package local.craftmcp.marvel.ironman;

import local.craftmcp.marvel.MarvelHeroesPlugin;
import org.bukkit.Color;
import org.bukkit.FluidCollisionMode;
import org.bukkit.Location;
import org.bukkit.Particle;
import org.bukkit.Sound;
import org.bukkit.World;
import org.bukkit.entity.ArmorStand;
import org.bukkit.entity.EnderCrystal;
import org.bukkit.entity.Entity;
import org.bukkit.entity.LivingEntity;
import org.bukkit.entity.Player;
import org.bukkit.entity.Vehicle;
import org.bukkit.scheduler.BukkitRunnable;
import org.bukkit.util.RayTraceResult;
import org.bukkit.util.Vector;

/**
 * A repulsor blast: a light-blue particle sphere that travels in a straight line and explodes on the
 * first block or entity it touches. Movement is simulated with a ray trace each tick, so nothing
 * vanilla (snowball, fireball…) is spawned.
 */
final class RepulsorBolt extends BukkitRunnable {

    private static final Particle.DustOptions SKY = new Particle.DustOptions(Color.fromRGB(110, 215, 255), 1.0f);
    private static final Particle.DustOptions SKY_SMALL = new Particle.DustOptions(Color.fromRGB(140, 225, 255), 0.7f);
    private static final Particle.DustOptions CORE = new Particle.DustOptions(Color.fromRGB(235, 255, 255), 1.3f);
    private static final double SPHERE_RADIUS = 0.32;
    private static final int SPHERE_POINTS = 22;
    private static final double HIT_RADIUS = 0.3;

    private final MarvelHeroesPlugin plugin;
    private final RepulsorGauntlet gauntlet;
    private final Player shooter;
    private final World world;
    private final Location pos;
    private final Vector velocity;
    private final int maxTicks;
    private int age;

    RepulsorBolt(MarvelHeroesPlugin plugin, RepulsorGauntlet gauntlet, Player shooter, Location start, Vector dir) {
        this.plugin = plugin;
        this.gauntlet = gauntlet;
        this.shooter = shooter;
        this.world = start.getWorld();
        this.pos = start.clone();
        double speed = plugin.getConfig().getDouble("repulsor.speed", 1.8);
        this.velocity = dir.clone().normalize().multiply(speed);
        this.maxTicks = plugin.getConfig().getInt("repulsor.max-ticks", 40);
    }

    void launch() {
        // Muzzle flash
        world.spawnParticle(Particle.FLASH, pos, 1, 0, 0, 0, 0, null, true);
        world.spawnParticle(Particle.END_ROD, pos, 10, 0.05, 0.05, 0.05, 0.12, null, true);
        world.spawnParticle(Particle.REDSTONE, pos, 14, 0.15, 0.15, 0.15, 0, SKY, true);
        world.playSound(pos, Sound.BLOCK_BEACON_ACTIVATE, 0.9f, 2.0f);
        world.playSound(pos, Sound.ENTITY_BLAZE_SHOOT, 0.5f, 1.9f);
        world.playSound(pos, Sound.ENTITY_ILLUSIONER_CAST_SPELL, 0.6f, 1.6f);

        // A hit right at the muzzle (point-blank wall) explodes immediately; otherwise tick every game tick.
        if (!step()) runTaskTimer(plugin, 1L, 1L);
    }

    @Override
    public void run() {
        if (step()) cancel();
    }

    /** Advances one tick. Returns true when the bolt is finished. */
    private boolean step() {
        if (!shooter.isOnline() || age++ > maxTicks) {
            fizzle();
            return true;
        }
        double distance = velocity.length();
        Vector dir = velocity.clone().normalize();
        RayTraceResult hit = world.rayTrace(pos, dir, distance, FluidCollisionMode.NEVER, true, HIT_RADIUS,
                this::canHit);

        Location end = hit != null ? hit.getHitPosition().toLocation(world) : pos.clone().add(velocity);
        trail(pos, end);

        if (hit != null) {
            // Pull back slightly from a block face so the blast is not buried inside the block.
            Location at = end.clone().subtract(dir.clone().multiply(hit.getHitBlock() != null ? 0.2 : 0));
            detonate(at);
            return true;
        }
        pos.add(velocity);
        if (!world.isChunkLoaded(pos.getBlockX() >> 4, pos.getBlockZ() >> 4)
                || pos.getY() < world.getMinHeight() || pos.getY() > world.getMaxHeight()) {
            return true;
        }
        sphere(pos);
        return false;
    }

    private boolean canHit(Entity e) {
        if (e == shooter || e.isDead()) return false;
        if (e instanceof Player p && p.getGameMode() == org.bukkit.GameMode.SPECTATOR) return false;
        if (e instanceof ArmorStand stand && stand.isMarker()) return false;
        return e instanceof LivingEntity || e instanceof Vehicle || e instanceof EnderCrystal;
    }

    private void sphere(Location center) {
        // Fibonacci sphere, rotated a little each tick so the orb appears to spin.
        double golden = Math.PI * (3 - Math.sqrt(5));
        double spin = age * 0.6;
        for (int i = 0; i < SPHERE_POINTS; i++) {
            double y = 1 - (i / (double) (SPHERE_POINTS - 1)) * 2;
            double r = Math.sqrt(1 - y * y);
            double theta = golden * i + spin;
            world.spawnParticle(Particle.REDSTONE,
                    center.getX() + Math.cos(theta) * r * SPHERE_RADIUS,
                    center.getY() + y * SPHERE_RADIUS,
                    center.getZ() + Math.sin(theta) * r * SPHERE_RADIUS,
                    1, 0, 0, 0, 0, SKY, true);
        }
        world.spawnParticle(Particle.REDSTONE, center, 3, 0.05, 0.05, 0.05, 0, CORE, true);
        world.spawnParticle(Particle.END_ROD, center, 1, 0.02, 0.02, 0.02, 0.0, null, true);
        if (age % 3 == 0) world.spawnParticle(Particle.ELECTRIC_SPARK, center, 2, 0.2, 0.2, 0.2, 0.05, null, true);
    }

    private void trail(Location from, Location to) {
        Vector delta = to.toVector().subtract(from.toVector());
        int points = Math.max(1, (int) Math.ceil(delta.length() / 0.3));
        for (int i = 0; i < points; i++) {
            Location p = from.clone().add(delta.clone().multiply(i / (double) points));
            world.spawnParticle(Particle.REDSTONE, p, 1, 0.04, 0.04, 0.04, 0, SKY_SMALL, true);
        }
    }

    private void detonate(Location at) {
        world.spawnParticle(Particle.FLASH, at, 2, 0, 0, 0, 0, null, true);
        world.spawnParticle(Particle.END_ROD, at, 45, 0.1, 0.1, 0.1, 0.32, null, true);
        world.spawnParticle(Particle.ELECTRIC_SPARK, at, 40, 0.6, 0.6, 0.6, 0.4, null, true);
        world.spawnParticle(Particle.REDSTONE, at, 60, 0.9, 0.9, 0.9, 0, SKY, true);
        world.spawnParticle(Particle.SONIC_BOOM, at, 1, 0, 0, 0, 0, null, true);
        world.playSound(at, Sound.BLOCK_BEACON_DEACTIVATE, 1.2f, 2.0f);
        world.playSound(at, Sound.ENTITY_FIREWORK_ROCKET_LARGE_BLAST, 1.2f, 0.8f);

        float power = (float) plugin.getConfig().getDouble("repulsor.explosion-power", 2.0);
        boolean fire = plugin.getConfig().getBoolean("repulsor.set-fire", false);
        boolean breakBlocks = plugin.getConfig().getBoolean("repulsor.break-blocks", false);
        gauntlet.explode(shooter, at, power, fire, breakBlocks);
    }

    private void fizzle() {
        world.spawnParticle(Particle.REDSTONE, pos, 12, 0.25, 0.25, 0.25, 0, SKY_SMALL, true);
        world.spawnParticle(Particle.END_ROD, pos, 4, 0.1, 0.1, 0.1, 0.05, null, true);
    }
}
