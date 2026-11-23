package local.craftmcp.marvel.thor;

import local.craftmcp.marvel.MarvelHeroesPlugin;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.Color;
import org.bukkit.FluidCollisionMode;
import org.bukkit.GameMode;
import org.bukkit.Location;
import org.bukkit.Particle;
import org.bukkit.Sound;
import org.bukkit.World;
import org.bukkit.entity.ArmorStand;
import org.bukkit.entity.Display;
import org.bukkit.entity.Entity;
import org.bukkit.entity.ItemDisplay;
import org.bukkit.entity.LivingEntity;
import org.bukkit.entity.Player;
import org.bukkit.event.Event;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.Action;
import org.bukkit.event.entity.PlayerDeathEvent;
import org.bukkit.event.inventory.InventoryClickEvent;
import org.bukkit.event.player.PlayerDropItemEvent;
import org.bukkit.event.player.PlayerInteractEntityEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerSwapHandItemsEvent;
import org.bukkit.inventory.EquipmentSlot;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.PlayerInventory;
import org.bukkit.scheduler.BukkitTask;
import org.bukkit.util.RayTraceResult;
import org.bukkit.util.Transformation;
import org.bukkit.util.Vector;
import org.joml.AxisAngle4f;
import org.joml.Vector3f;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Throwing and recalling Mjolnir.
 * <ol>
 *   <li>Right-click with the hammer: it flies off spinning. The first mob it hits takes damage and is
 *       knocked back; the hammer then hangs where it struck. A block, or running out of range, also stops it.</li>
 *   <li>The hand keeps the hammer item in a "thrown" state (just an outline).</li>
 *   <li>Right-click again: the hammer flies straight back through anything in the way, hitting mobs
 *       on its path, and is caught.</li>
 * </ol>
 * The flying hammer is an {@link ItemDisplay}, moved by one per-tick task.
 */
public final class Mjolnir implements Listener {

    private enum State { OUT, HOVER, RETURN }

    private static final Particle.DustOptions SPARK = new Particle.DustOptions(Color.fromRGB(150, 210, 255), 0.9f);

    private static final class Throw {
        final ItemDisplay display;
        final Location pos;
        Vector dir;
        State state = State.OUT;
        double travelled;
        int ticks;
        float spin;
        final Set<UUID> hit = new HashSet<>();

        Throw(ItemDisplay display, Location pos, Vector dir) {
            this.display = display;
            this.pos = pos;
            this.dir = dir;
        }
    }

    private final MarvelHeroesPlugin plugin;
    private final Map<UUID, Throw> hammers = new HashMap<>();
    private final Map<UUID, Long> lastClick = new HashMap<>();
    private BukkitTask task;
    private long tick;

    public Mjolnir(MarvelHeroesPlugin plugin) {
        this.plugin = plugin;
    }

    public void start() {
        task = Bukkit.getScheduler().runTaskTimer(plugin, this::tickAll, 1L, 1L);
    }

    public void shutdown() {
        if (task != null) task.cancel();
        for (UUID id : new HashSet<>(hammers.keySet())) {
            Player p = Bukkit.getPlayer(id);
            if (p != null) catchHammer(p, false);
        }
        hammers.clear();
    }

    // ------------------------------------------------------------------ input

    @EventHandler(priority = EventPriority.HIGH)
    public void onInteract(PlayerInteractEvent event) {
        if (event.getHand() != EquipmentSlot.HAND || !ThorHammer.is(event.getItem())) return;
        Action a = event.getAction();
        if (a != Action.RIGHT_CLICK_AIR && a != Action.RIGHT_CLICK_BLOCK) return;
        event.setUseItemInHand(Event.Result.DENY);
        event.setUseInteractedBlock(Event.Result.DENY);
        rightClick(event.getPlayer());
    }

    @EventHandler(priority = EventPriority.HIGH)
    public void onInteractEntity(PlayerInteractEntityEvent event) {
        if (event.getHand() != EquipmentSlot.HAND) return;
        Player p = event.getPlayer();
        if (!ThorHammer.is(p.getInventory().getItemInMainHand())) return;
        event.setCancelled(true);
        rightClick(p);
    }

    private void rightClick(Player p) {
        long now = System.currentTimeMillis();
        Long last = lastClick.get(p.getUniqueId());
        if (last != null && now - last < 250) return; // one click can arrive as two events
        lastClick.put(p.getUniqueId(), now);

        ItemStack hand = p.getInventory().getItemInMainHand();
        Throw t = hammers.get(p.getUniqueId());
        if (t != null) {
            if (t.state != State.RETURN) recall(p, t);
        } else if (!ThorHammer.isThrown(hand)) {
            throwHammer(p, hand);
        }
    }

    // ------------------------------------------------------------------ actions

    private void throwHammer(Player p, ItemStack hand) {
        Location eye = p.getEyeLocation();
        Vector dir = eye.getDirection().normalize();
        Location start = eye.clone().add(dir.clone().multiply(0.8)).add(0, -0.2, 0);
        World w = p.getWorld();

        ItemDisplay display = w.spawn(start, ItemDisplay.class, d -> {
            d.setItemStack(ThorHammer.flyingModel());
            d.setItemDisplayTransform(ItemDisplay.ItemDisplayTransform.FIXED);
            d.setBillboard(Display.Billboard.FIXED);
            d.setPersistent(false);
            d.setBrightness(new Display.Brightness(15, 15));
            d.setRotation(start.getYaw(), 0);
        });
        hammers.put(p.getUniqueId(), new Throw(display, start, dir));
        ThorHammer.setThrown(hand, true);
        p.getInventory().setItemInMainHand(hand);

        w.playSound(start, Sound.ITEM_TRIDENT_THROW, 1.0f, 0.6f);
        w.playSound(start, Sound.ENTITY_PLAYER_ATTACK_SWEEP, 0.8f, 0.7f);
        p.swingMainHand();
        plugin.debug("[thor] " + p.getName() + " throw dir=" + vec(dir));
    }

    private void recall(Player p, Throw t) {
        t.state = State.RETURN;
        t.ticks = 0;
        // t.hit keeps the mob struck on the way out, so the return trip does not hit it a second
        // time right where the hammer starts from.
        p.getWorld().playSound(p.getLocation(), Sound.ITEM_TRIDENT_RETURN, 1.0f, 0.8f);
        t.pos.getWorld().playSound(t.pos, Sound.BLOCK_BEACON_POWER_SELECT, 0.8f, 1.6f);
        p.swingMainHand();
        plugin.debug("[thor] " + p.getName() + " recall from " + vec(t.pos.toVector())
                + " dist=" + fmt(t.pos.getWorld() == p.getWorld() ? t.pos.distance(p.getLocation()) : -1));
    }

    /** Hammer back in hand outside the tick loop (quit, disable): remove the model, restore the item. */
    private void catchHammer(Player p, boolean effects) {
        Throw t = hammers.remove(p.getUniqueId());
        if (t != null && t.display.isValid()) t.display.remove();
        restoreItems(p.getInventory());
        if (effects) catchEffects(p);
    }

    private void catchEffects(Player p) {
        p.getWorld().playSound(p.getLocation(), Sound.ITEM_TRIDENT_RETURN, 1.0f, 1.2f);
        p.getWorld().playSound(p.getLocation(), Sound.BLOCK_ANVIL_PLACE, 0.4f, 1.8f);
        p.getWorld().spawnParticle(Particle.ELECTRIC_SPARK, p.getLocation().add(0, 1.2, 0), 15, 0.3, 0.3, 0.3, 0.1);
        p.sendActionBar(Component.text("묠니르가 돌아왔습니다.", NamedTextColor.AQUA));
    }

    private static void restoreItems(PlayerInventory inv) {
        for (int i = 0; i < inv.getSize(); i++) {
            ItemStack it = inv.getItem(i);
            if (ThorHammer.isThrown(it)) {
                ThorHammer.setThrown(it, false);
                inv.setItem(i, it);
            }
        }
    }

    // ------------------------------------------------------------------ per tick

    private void tickAll() {
        tick++;
        for (Iterator<Map.Entry<UUID, Throw>> it = hammers.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<UUID, Throw> e = it.next();
            Player p = Bukkit.getPlayer(e.getKey());
            Throw t = e.getValue();
            if (p == null) {
                if (t.display.isValid()) t.display.remove();
                it.remove();
                continue;
            }
            boolean done = switch (t.state) {
                case OUT -> stepOut(p, t);
                case HOVER -> stepHover(p, t);
                case RETURN -> stepReturn(p, t);
            };
            if (done) {
                it.remove();
                if (t.display.isValid()) t.display.remove();
                restoreItems(p.getInventory());
            }
        }
        // A "thrown" item with no hammer in the air (server restart, crash...) goes back to normal.
        if (tick % 20 == 0) {
            for (Player p : Bukkit.getOnlinePlayers()) {
                if (!hammers.containsKey(p.getUniqueId())) restoreItems(p.getInventory());
            }
        }
    }

    private boolean stepOut(Player p, Throw t) {
        World w = t.pos.getWorld();
        double speed = plugin.getConfig().getDouble("thor.throw-speed", 1.8);
        double max = plugin.getConfig().getDouble("thor.max-distance", 40);
        RayTraceResult hit = w.rayTrace(t.pos, t.dir, speed, FluidCollisionMode.NEVER, true, 0.4,
                e -> canHit(p, e));
        if (hit == null) {
            trail(t.pos, t.dir, speed);
            t.pos.add(t.dir.clone().multiply(speed));
            t.travelled += speed;
            if (t.travelled >= max) {
                t.state = State.HOVER;
                plugin.debug("[thor] " + p.getName() + " out-of-range at " + vec(t.pos.toVector()));
            }
        } else {
            Location at = hit.getHitPosition().toLocation(w);
            trail(t.pos, t.dir, t.pos.distance(at));
            if (hit.getHitEntity() != null) {
                strike(p, (LivingEntity) hit.getHitEntity(), t.dir);
                t.hit.add(hit.getHitEntity().getUniqueId());
                t.pos.setX(at.getX());
                t.pos.setY(at.getY());
                t.pos.setZ(at.getZ());
            } else {
                // Stop just in front of the block face.
                Location back = at.clone().subtract(t.dir.clone().multiply(0.35));
                t.pos.setX(back.getX());
                t.pos.setY(back.getY());
                t.pos.setZ(back.getZ());
                w.playSound(at, Sound.BLOCK_ANVIL_LAND, 0.6f, 0.8f);
                w.spawnParticle(Particle.BLOCK_CRACK, at, 20, 0.2, 0.2, 0.2, 0, hit.getHitBlock().getBlockData());
                plugin.debug("[thor] " + p.getName() + " hit-block " + hit.getHitBlock().getType() + " at " + vec(at.toVector()));
            }
            t.state = State.HOVER;
        }
        render(t, 0.55f);
        return false;
    }

    private boolean stepHover(Player p, Throw t) {
        if (t.pos.getWorld() != p.getWorld()) return true; // player left the world: back to hand
        render(t, 0.08f);
        if (tick % 10 == 0) t.pos.getWorld().spawnParticle(Particle.ELECTRIC_SPARK, t.pos, 2, 0.15, 0.15, 0.15, 0.02);
        return false;
    }

    private boolean stepReturn(Player p, Throw t) {
        if (t.pos.getWorld() != p.getWorld() || ++t.ticks > 200) {
            catchEffects(p);
            return true;
        }
        Location hand = p.getEyeLocation().add(0, -0.5, 0);
        Vector delta = hand.toVector().subtract(t.pos.toVector());
        double dist = delta.length();
        if (dist < 1.3) {
            plugin.debug("[thor] " + p.getName() + " caught after " + t.ticks + " ticks");
            catchEffects(p);
            return true;
        }
        double speed = Math.min(plugin.getConfig().getDouble("thor.return-speed", 2.0), dist);
        t.dir = delta.normalize();
        // On the way back the hammer ignores blocks but still hits mobs in its path.
        for (Entity e : t.pos.getWorld().getNearbyEntities(t.pos.clone().add(t.dir.clone().multiply(speed / 2)),
                speed / 2 + 0.6, speed / 2 + 0.6, speed / 2 + 0.6, e -> canHit(p, e))) {
            if (t.hit.add(e.getUniqueId())) strike(p, (LivingEntity) e, t.dir);
        }
        trail(t.pos, t.dir, speed);
        t.pos.add(t.dir.clone().multiply(speed));
        render(t, 0.6f);
        return false;
    }

    private boolean canHit(Player thrower, Entity e) {
        if (e == thrower || e.isDead() || !(e instanceof LivingEntity)) return false;
        if (e instanceof Player p && p.getGameMode() == GameMode.SPECTATOR) return false;
        return !(e instanceof ArmorStand stand && stand.isMarker());
    }

    private void strike(Player p, LivingEntity target, Vector dir) {
        double damage = plugin.getConfig().getDouble("thor.damage", 10.0);
        double before = target.getHealth();
        target.damage(damage, p);
        target.setVelocity(dir.clone().setY(0).normalize().multiply(1.1).setY(0.45));
        Location at = target.getLocation().add(0, target.getHeight() / 2, 0);
        World w = target.getWorld();
        w.spawnParticle(Particle.FLASH, at, 1, 0, 0, 0, 0);
        w.spawnParticle(Particle.ELECTRIC_SPARK, at, 30, 0.4, 0.5, 0.4, 0.25);
        w.spawnParticle(Particle.REDSTONE, at, 20, 0.4, 0.5, 0.4, 0, SPARK);
        w.playSound(at, Sound.ENTITY_LIGHTNING_BOLT_IMPACT, 0.8f, 1.3f);
        w.playSound(at, Sound.ENTITY_IRON_GOLEM_ATTACK, 1.0f, 0.8f);
        plugin.debug("[thor] " + p.getName() + " hit " + target.getType() + " health " + fmt(before)
                + " -> " + fmt(target.getHealth()));
    }

    /** Moves the display to the hammer position and spins it end over end. */
    private void render(Throw t, float spinPerTick) {
        ItemDisplay d = t.display;
        if (!d.isValid()) return;
        Location at = t.pos.clone();
        at.setDirection(t.dir.clone().setY(0).lengthSquared() > 1.0e-6 ? t.dir.clone().setY(0) : new Vector(0, 0, 1));
        at.setPitch(0);
        d.teleport(at);
        t.spin += spinPerTick;
        d.setInterpolationDelay(0);
        d.setInterpolationDuration(1);
        d.setTransformation(new Transformation(new Vector3f(), new AxisAngle4f(t.spin, 1, 0, 0),
                new Vector3f(1.6f, 1.6f, 1.6f), new AxisAngle4f()));
    }

    private static void trail(Location from, Vector dir, double length) {
        World w = from.getWorld();
        for (double s = 0; s < length; s += 0.5) {
            w.spawnParticle(Particle.REDSTONE, from.clone().add(dir.clone().multiply(s)), 1, 0.05, 0.05, 0.05, 0, SPARK, true);
        }
    }

    // ------------------------------------------------------------------ safety

    /** The thrown-state item cannot be moved, dropped or swapped while the hammer is away. */
    @EventHandler(priority = EventPriority.LOWEST)
    public void onClick(InventoryClickEvent event) {
        if (ThorHammer.isThrown(event.getCurrentItem()) || ThorHammer.isThrown(event.getCursor())) event.setCancelled(true);
        if (event.getHotbarButton() >= 0 && event.getWhoClicked() instanceof Player p
                && ThorHammer.isThrown(p.getInventory().getItem(event.getHotbarButton()))) {
            event.setCancelled(true);
        }
    }

    @EventHandler(priority = EventPriority.LOWEST)
    public void onDrop(PlayerDropItemEvent event) {
        if (ThorHammer.isThrown(event.getItemDrop().getItemStack())) event.setCancelled(true);
    }

    @EventHandler(priority = EventPriority.LOWEST)
    public void onSwap(PlayerSwapHandItemsEvent event) {
        if (ThorHammer.isThrown(event.getMainHandItem()) || ThorHammer.isThrown(event.getOffHandItem())) {
            event.setCancelled(true);
        }
    }

    @EventHandler
    public void onDeath(PlayerDeathEvent event) {
        Throw t = hammers.remove(event.getEntity().getUniqueId());
        if (t != null && t.display.isValid()) t.display.remove();
        for (ItemStack drop : event.getDrops()) if (ThorHammer.isThrown(drop)) ThorHammer.setThrown(drop, false);
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        Player p = event.getPlayer();
        if (hammers.containsKey(p.getUniqueId())) catchHammer(p, false);
        lastClick.remove(p.getUniqueId());
    }

    private static String fmt(double d) {
        return String.format("%.2f", d);
    }

    private static String vec(Vector v) {
        return String.format("(%.1f, %.1f, %.1f)", v.getX(), v.getY(), v.getZ());
    }
}
