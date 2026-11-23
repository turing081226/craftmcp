package local.craftmcp.marvel.spiderman;

import local.craftmcp.marvel.MarvelHeroesPlugin;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.Color;
import org.bukkit.FluidCollisionMode;
import org.bukkit.GameMode;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.Particle;
import org.bukkit.Sound;
import org.bukkit.World;
import org.bukkit.block.BlockFace;
import org.bukkit.entity.ArmorStand;
import org.bukkit.entity.Entity;
import org.bukkit.entity.LivingEntity;
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
import org.bukkit.scheduler.BukkitTask;
import org.bukkit.util.RayTraceResult;
import org.bukkit.util.Vector;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;
import java.util.UUID;

/**
 * Web shooter (hotbar slot 9 while the suit is worn).
 * <ol>
 *   <li>Right-click: a web line flies out.</li>
 *   <li>It sticks to the first entity or block it touches, and the line stays visible.</li>
 *   <li>Right-click again: an entity is pulled to just in front of the player; a block anchor zips
 *       the player to that spot.</li>
 * </ol>
 * Everything runs from one per-tick task: flying webs, attached lines, pulls and zips.
 */
public final class WebShooter implements Listener {

    private static final Particle.DustOptions WEB = new Particle.DustOptions(Color.fromRGB(245, 245, 245), 0.55f);
    private static final Particle.DustOptions WEB_TIP = new Particle.DustOptions(Color.WHITE, 1.1f);

    /** A web in flight. */
    private static final class Shot {
        final Location pos;
        final Vector vel;
        int age;

        Shot(Location pos, Vector vel) {
            this.pos = pos;
            this.vel = vel;
        }
    }

    /** A web stuck to something, waiting for the second right-click. */
    private static final class Line {
        Entity target;      // either an entity...
        Location anchor;    // ...or a point on a block
        Vector normal;      // block face normal at the anchor
        long expiresTick;
    }

    /** An active pull (entity -> player) or zip (player -> anchor). */
    private static final class Move {
        Entity target;      // null for a zip
        Location dest;      // zip destination (player feet)
        Vector normal;
        int ticks;
        Location lastPos;
        int stuckTicks;
        boolean started;    // the player has actually begun to move (server position lags the client)
    }

    private final MarvelHeroesPlugin plugin;
    private final Map<UUID, Shot> shots = new HashMap<>();
    private final Map<UUID, Line> lines = new HashMap<>();
    private final Map<UUID, Move> moves = new HashMap<>();
    private final Map<UUID, Long> lastClickMs = new HashMap<>();
    private final Map<UUID, Long> lastShotMs = new HashMap<>();
    /** Short fall-damage grace after a zip ends, as a tick deadline. */
    private final Map<UUID, Long> graceUntil = new HashMap<>();
    private BukkitTask task;
    private long tick;

    public WebShooter(MarvelHeroesPlugin plugin) {
        this.plugin = plugin;
    }

    public void start() {
        task = Bukkit.getScheduler().runTaskTimer(plugin, this::tickAll, 1L, 1L);
    }

    public void shutdown() {
        if (task != null) task.cancel();
        shots.clear();
        lines.clear();
        moves.clear();
    }

    /** True while the player is being zipped to an anchor (wall crawling should not fight it). */
    public boolean isZipping(Player p) {
        Move m = moves.get(p.getUniqueId());
        return m != null && m.target == null;
    }

    // ------------------------------------------------------------------ input

    @EventHandler(priority = EventPriority.HIGH)
    public void onInteract(PlayerInteractEvent event) {
        if (event.getHand() != EquipmentSlot.HAND || !SpiderSuit.isWebShooter(event.getItem())) return;
        event.setUseItemInHand(Event.Result.DENY);
        event.setUseInteractedBlock(Event.Result.DENY);
        Action a = event.getAction();
        if (a == Action.RIGHT_CLICK_AIR || a == Action.RIGHT_CLICK_BLOCK) rightClick(event.getPlayer());
    }

    @EventHandler(priority = EventPriority.HIGH)
    public void onInteractEntity(PlayerInteractEntityEvent event) {
        if (event.getHand() != EquipmentSlot.HAND) return;
        Player p = event.getPlayer();
        if (!SpiderSuit.isWebShooter(p.getInventory().getItemInMainHand())) return;
        event.setCancelled(true);
        rightClick(p);
    }

    private void rightClick(Player p) {
        if (!SpiderSuit.wearsSuit(p) || p.getInventory().getHeldItemSlot() != SpiderSuit.WEB_SLOT) return;
        UUID id = p.getUniqueId();
        long now = System.currentTimeMillis();
        // One physical click can arrive as two events (block + air); treat clicks this close as one.
        Long last = lastClickMs.get(id);
        if (last != null && now - last < 200) return;
        lastClickMs.put(id, now);

        Line line = lines.remove(id);
        if (line != null) {
            if (line.target != null) startPull(p, line.target);
            else startZip(p, line);
            return;
        }
        if (shots.containsKey(id) || moves.containsKey(id)) return;
        Long lastShot = lastShotMs.get(id);
        if (lastShot != null && now - lastShot < plugin.getConfig().getLong("spiderman.web.cooldown-ms", 300)) return;
        lastShotMs.put(id, now);
        shoot(p);
    }

    // ------------------------------------------------------------------ actions

    private void shoot(Player p) {
        Location start = hand(p);
        double speed = plugin.getConfig().getDouble("spiderman.web.speed", 2.5);
        shots.put(p.getUniqueId(), new Shot(start, p.getEyeLocation().getDirection().normalize().multiply(speed)));
        World w = p.getWorld();
        w.playSound(start, Sound.ENTITY_FISHING_BOBBER_THROW, 0.9f, 1.6f);
        w.playSound(start, Sound.BLOCK_WOOL_PLACE, 0.6f, 1.8f);
        p.swingMainHand();
        plugin.debug("[spider] " + p.getName() + " web-shoot");
    }

    private void startPull(Player p, Entity target) {
        if (!target.isValid()) return;
        Move m = new Move();
        m.target = target;
        moves.put(p.getUniqueId(), m);
        p.getWorld().playSound(p.getLocation(), Sound.ENTITY_FISHING_BOBBER_RETRIEVE, 1.0f, 0.8f);
        p.swingMainHand();
        plugin.debug("[spider] " + p.getName() + " pull " + target.getType() + " dist="
                + fmt(target.getLocation().distance(p.getLocation())));
    }

    private void startZip(Player p, Line line) {
        Move m = new Move();
        m.normal = line.normal;
        // Aim the feet so the body ends up just off the face, hands at the anchor.
        if (line.normal.getY() > 0.5) {
            m.dest = line.anchor.clone().add(0, 0.1, 0);                              // top of a block
        } else if (line.normal.getY() < -0.5) {
            m.dest = line.anchor.clone().add(0, -2.0, 0);                             // ceiling
        } else {
            m.dest = line.anchor.clone().add(line.normal.clone().multiply(0.35)).add(0, -1.2, 0); // wall
        }
        moves.put(p.getUniqueId(), m);
        p.getWorld().playSound(p.getLocation(), Sound.ENTITY_FISHING_BOBBER_RETRIEVE, 1.0f, 1.2f);
        p.getWorld().playSound(p.getLocation(), Sound.ITEM_TRIDENT_RIPTIDE_1, 0.5f, 1.6f);
        p.swingMainHand();
        plugin.debug("[spider] " + p.getName() + " zip to " + vec(m.dest) + " dist="
                + fmt(m.dest.distance(p.getLocation())));
    }

    // ------------------------------------------------------------------ per tick

    private void tickAll() {
        tick++;
        for (Iterator<Map.Entry<UUID, Shot>> it = shots.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<UUID, Shot> e = it.next();
            Player p = Bukkit.getPlayer(e.getKey());
            if (p == null || !SpiderSuit.wearsSuit(p) || stepShot(p, e.getValue())) it.remove();
        }
        for (Iterator<Map.Entry<UUID, Line>> it = lines.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<UUID, Line> e = it.next();
            Player p = Bukkit.getPlayer(e.getKey());
            Line line = e.getValue();
            Location end = line.target != null ? center(line.target) : line.anchor;
            double maxLength = plugin.getConfig().getDouble("spiderman.web.max-length", 48);
            if (p == null || !SpiderSuit.wearsSuit(p) || tick > line.expiresTick
                    || (line.target != null && !line.target.isValid())
                    || end.getWorld() != p.getWorld() || end.distance(hand(p)) > maxLength) {
                if (p != null) p.sendActionBar(Component.text("거미줄이 끊어졌습니다.", NamedTextColor.GRAY));
                it.remove();
                continue;
            }
            drawLine(hand(p), end);
        }
        for (Iterator<Map.Entry<UUID, Move>> it = moves.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<UUID, Move> e = it.next();
            Player p = Bukkit.getPlayer(e.getKey());
            Move m = e.getValue();
            boolean done = p == null || !SpiderSuit.wearsSuit(p)
                    || (m.target != null ? stepPull(p, m) : stepZip(p, m));
            if (done) it.remove();
        }
    }

    /** Returns true when the shot is finished (hit or out of range). */
    private boolean stepShot(Player p, Shot s) {
        World w = s.pos.getWorld();
        if (w != p.getWorld() || ++s.age > plugin.getConfig().getInt("spiderman.web.max-ticks", 16)) {
            p.sendActionBar(Component.text("거미줄이 닿지 않았습니다.", NamedTextColor.GRAY));
            return true;
        }
        Vector dir = s.vel.clone().normalize();
        RayTraceResult hit = w.rayTrace(s.pos, dir, s.vel.length(), FluidCollisionMode.NEVER, true, 0.25,
                e -> canHit(p, e));
        Location tip = hit != null ? hit.getHitPosition().toLocation(w) : s.pos.clone().add(s.vel);
        drawLine(hand(p), tip);
        w.spawnParticle(Particle.REDSTONE, tip, 3, 0.05, 0.05, 0.05, 0, WEB_TIP, true);

        if (hit == null) {
            s.pos.add(s.vel);
            return false;
        }
        Line line = new Line();
        line.expiresTick = tick + 20L * plugin.getConfig().getInt("spiderman.web.attach-seconds", 15);
        if (hit.getHitEntity() != null) {
            line.target = hit.getHitEntity();
            p.sendActionBar(Component.text("거미줄 명중! 다시 우클릭하면 끌어당깁니다.", NamedTextColor.WHITE));
            plugin.debug("[spider] " + p.getName() + " web-hit entity " + line.target.getType());
        } else {
            BlockFace face = hit.getHitBlockFace() != null ? hit.getHitBlockFace() : BlockFace.UP;
            line.anchor = tip;
            line.normal = face.getDirection();
            p.sendActionBar(Component.text("거미줄 고정! 다시 우클릭하면 그곳으로 날아갑니다.", NamedTextColor.WHITE));
            plugin.debug("[spider] " + p.getName() + " web-hit block " + hit.getHitBlock().getType()
                    + " face=" + face + " at " + vec(tip));
        }
        lines.put(p.getUniqueId(), line);
        w.spawnParticle(Particle.BLOCK_CRACK, tip, 12, 0.15, 0.15, 0.15, 0, Material.COBWEB.createBlockData(), true);
        w.playSound(tip, Sound.ENTITY_SLIME_SQUISH_SMALL, 0.8f, 1.4f);
        return true;
    }

    private boolean canHit(Player shooter, Entity e) {
        if (e == shooter || e.isDead() || !(e instanceof LivingEntity)) return false;
        if (e instanceof Player p && p.getGameMode() == GameMode.SPECTATOR) return false;
        return !(e instanceof ArmorStand stand && stand.isMarker());
    }

    /** Pulls the target to ~2 blocks in front of the player. Returns true when finished. */
    private boolean stepPull(Player p, Move m) {
        Entity t = m.target;
        if (!t.isValid() || t.getWorld() != p.getWorld() || ++m.ticks > 40) return true;
        Vector look = p.getLocation().getDirection().setY(0);
        if (look.lengthSquared() < 1.0e-4) look = new Vector(0, 0, 0);
        else look.normalize().multiply(2.0);
        Location dest = p.getLocation().add(look).add(0, 0.2, 0);
        Vector delta = dest.toVector().subtract(t.getLocation().toVector());
        drawLine(hand(p), center(t));
        if (delta.length() < 1.0) {
            t.setVelocity(new Vector(0, 0.1, 0));
            Vector rel = t.getLocation().toVector().subtract(p.getLocation().toVector()).setY(0);
            boolean inFront = rel.dot(p.getLocation().getDirection().setY(0)) > 0;
            plugin.debug("[spider] " + p.getName() + " pull-done " + t.getType() + " dist="
                    + fmt(t.getLocation().distance(p.getLocation())) + " inFront=" + inFront + " ticks=" + m.ticks);
            return true;
        }
        double speed = Math.min(plugin.getConfig().getDouble("spiderman.web.pull-speed", 1.2), delta.length() * 0.5);
        Vector v = delta.clone().normalize().multiply(speed);
        v.setY(v.getY() + 0.08); // counter gravity a little so it does not drag along the ground
        t.setVelocity(v);
        return false;
    }

    /** Moves the player to the anchor. Returns true when finished. */
    private boolean stepZip(Player p, Move m) {
        Location loc = p.getLocation();
        if (m.dest.getWorld() != loc.getWorld() || ++m.ticks > 60) return finishZip(p, m, "timeout");
        Vector delta = m.dest.toVector().subtract(loc.toVector());
        drawLine(hand(p), m.dest.clone().add(0, 1.2, 0));
        boolean toWall = m.normal != null && Math.abs(m.normal.getY()) < 0.5;
        // Get right up against a wall so wall crawling picks the player up on arrival.
        if (delta.length() < (toWall ? 0.4 : 0.8)) return finishZip(p, m, "arrived");
        // Blocked by something on the way: stop instead of pushing forever. Only judged once the
        // player has started moving, because under server lag the position can sit still for a
        // few ticks before the client's movement arrives.
        if (m.lastPos != null && m.lastPos.distanceSquared(loc) < 0.01) {
            if (m.started && ++m.stuckTicks > 5) return finishZip(p, m, "blocked");
        } else {
            if (m.lastPos != null) m.started = true;
            m.stuckTicks = 0;
        }
        m.lastPos = loc;
        double speed = Math.min(plugin.getConfig().getDouble("spiderman.web.zip-speed", 1.4), delta.length() * 0.6 + 0.2);
        p.setVelocity(delta.clone().normalize().multiply(speed));
        p.setFallDistance(0);
        if (m.ticks % 3 == 0) p.getWorld().spawnParticle(Particle.CLOUD, loc, 2, 0.2, 0.2, 0.2, 0.01);
        return false;
    }

    private boolean finishZip(Player p, Move m, String why) {
        // Arriving at a wall: stop dead so wall crawling can take over; elsewhere keep a little momentum.
        boolean wall = m.normal != null && Math.abs(m.normal.getY()) < 0.5;
        p.setVelocity(wall ? m.normal.clone().multiply(-0.2).setY(0.05) : p.getVelocity().multiply(0.3));
        p.setFallDistance(0);
        graceUntil.put(p.getUniqueId(), tick + 40);
        plugin.debug("[spider] " + p.getName() + " zip-" + why + " at " + vec(p.getLocation())
                + " dest=" + vec(m.dest) + " ticks=" + m.ticks);
        return true;
    }

    @EventHandler(ignoreCancelled = true)
    public void onFall(EntityDamageEvent event) {
        if (event.getCause() != EntityDamageEvent.DamageCause.FALL || !(event.getEntity() instanceof Player p)) return;
        Long grace = graceUntil.get(p.getUniqueId());
        if (moves.containsKey(p.getUniqueId()) || (grace != null && tick <= grace)) event.setCancelled(true);
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        UUID id = event.getPlayer().getUniqueId();
        shots.remove(id);
        lines.remove(id);
        moves.remove(id);
        lastClickMs.remove(id);
        lastShotMs.remove(id);
        graceUntil.remove(id);
    }

    // ------------------------------------------------------------------ helpers

    private static Location hand(Player p) {
        Location eye = p.getEyeLocation();
        Vector dir = eye.getDirection().normalize();
        Vector right = dir.clone().crossProduct(new Vector(0, 1, 0));
        if (right.lengthSquared() > 1.0e-6) right.normalize().multiply(0.35);
        return eye.add(dir.multiply(0.4)).add(right).add(0, -0.35, 0);
    }

    private static Location center(Entity e) {
        return e.getLocation().add(0, e.getHeight() / 2, 0);
    }

    private static void drawLine(Location from, Location to) {
        Vector delta = to.toVector().subtract(from.toVector());
        double length = delta.length();
        int points = (int) Math.min(120, Math.ceil(length / 0.4));
        World w = from.getWorld();
        for (int i = 0; i <= points; i++) {
            Location at = from.clone().add(delta.clone().multiply(points == 0 ? 0 : i / (double) points));
            w.spawnParticle(Particle.REDSTONE, at, 1, 0, 0, 0, 0, WEB, true);
        }
    }

    private static String fmt(double d) {
        return String.format("%.2f", d);
    }

    private static String vec(Location l) {
        return String.format("(%.1f, %.1f, %.1f)", l.getX(), l.getY(), l.getZ());
    }
}
