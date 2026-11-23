package local.craftmcp.marvel.spiderman;

import local.craftmcp.marvel.MarvelHeroesPlugin;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.Sound;
import org.bukkit.block.Block;
import org.bukkit.entity.Player;
import org.bukkit.scheduler.BukkitTask;
import org.bukkit.util.Vector;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Predicate;

/**
 * Wall crawling. A suited player who faces a wall right in front of them climbs it:
 * <ul>
 *   <li>facing a wall: climb up</li>
 *   <li>sneaking against a wall in the air: cling in place</li>
 *   <li>reaching the top edge: a small hop onto the ledge</li>
 *   <li>turning away from the wall: let go</li>
 * </ul>
 * The server cannot see movement keys in 1.20.1, so "wants to climb" is read from where the player
 * looks. A small push into the wall keeps the player touching it, which also keeps the server's
 * "flying is not enabled" check quiet while they hang there.
 */
public final class WallCrawl {

    private static final double REACH = 0.7; // from the body centre: 0.3 half-width + 0.4 gap
    private static final int HOP_COOLDOWN = 10;

    private final MarvelHeroesPlugin plugin;
    private final Predicate<Player> busy;
    private final Set<UUID> climbing = new HashSet<>();
    private final Map<UUID, Long> lastHop = new HashMap<>();
    private BukkitTask task;
    private long tick;

    /** @param busy players to leave alone this tick (e.g. while a web zip moves them) */
    public WallCrawl(MarvelHeroesPlugin plugin, Predicate<Player> busy) {
        this.plugin = plugin;
        this.busy = busy;
    }

    public void start() {
        task = Bukkit.getScheduler().runTaskTimer(plugin, () -> {
            tick++;
            for (Player p : Bukkit.getOnlinePlayers()) update(p);
        }, 1L, 1L);
    }

    public void shutdown() {
        if (task != null) task.cancel();
        climbing.clear();
        lastHop.clear();
    }

    public boolean isClimbing(Player p) {
        return climbing.contains(p.getUniqueId());
    }

    private void update(Player p) {
        UUID id = p.getUniqueId();
        if (!SpiderSuit.wearsSuit(p) || busy.test(p) || p.isFlying() || p.isGliding() || p.isInsideVehicle()
                || p.isInWater() || p.isInLava() || p.isSleeping()) {
            stop(p);
            return;
        }
        Vector look = p.getLocation().getDirection().setY(0);
        if (look.lengthSquared() < 1.0e-4) { // looking straight up/down: no facing direction
            stop(p);
            return;
        }
        look.normalize();

        Location base = p.getLocation();
        boolean feet = solid(base.clone().add(0, 0.2, 0).add(look.clone().multiply(REACH)));
        boolean body = solid(base.clone().add(0, 1.0, 0).add(look.clone().multiply(REACH)));
        boolean head = solid(base.clone().add(0, 1.7, 0).add(look.clone().multiply(REACH)));
        if (!feet && !body && !head) {
            stop(p);
            return;
        }
        // Standing on the ground sneaking = just standing there.
        if (p.isOnGround() && p.isSneaking()) {
            stop(p);
            return;
        }

        double speed = plugin.getConfig().getDouble("spiderman.climb-speed", 0.22);
        Vector v;
        if (p.isSneaking()) {
            v = look.clone().multiply(0.05);                  // cling
            v.setY(0);
        } else if (body || head) {
            v = look.clone().multiply(0.08).setY(speed);      // climb
        } else {
            // Top edge: one gentle hop onto the ledge. Repeating it every tick would add up and
            // throw the player clean over a thin wall.
            Long hop = lastHop.get(id);
            if (hop != null && tick - hop < HOP_COOLDOWN) return;
            lastHop.put(id, tick);
            v = look.clone().multiply(0.18).setY(0.42);
            plugin.debug("[spider] " + p.getName() + " ledge-hop y=" + fmt(base.getY()));
        }
        p.setVelocity(v);
        p.setFallDistance(0);

        if (climbing.add(id)) plugin.debug("[spider] " + p.getName() + " climb-start y=" + fmt(base.getY()));
        if (tick % 6 == 0) p.getWorld().playSound(base, Sound.ENTITY_SPIDER_STEP, 0.35f, 1.4f);
    }

    private void stop(Player p) {
        if (climbing.remove(p.getUniqueId())) {
            plugin.debug("[spider] " + p.getName() + " climb-stop y=" + fmt(p.getLocation().getY()));
        }
    }

    private static boolean solid(Location at) {
        Block b = at.getBlock();
        return b.getType().isSolid() && !b.isPassable();
    }

    private static String fmt(double d) {
        return String.format("%.2f", d);
    }
}
