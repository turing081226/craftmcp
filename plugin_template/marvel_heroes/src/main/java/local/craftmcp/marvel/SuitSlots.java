package local.craftmcp.marvel;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.Sound;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.PlayerDeathEvent;
import org.bukkit.event.inventory.ClickType;
import org.bukkit.event.inventory.InventoryClickEvent;
import org.bukkit.event.inventory.InventoryDragEvent;
import org.bukkit.event.player.PlayerArmorStandManipulateEvent;
import org.bukkit.event.player.PlayerDropItemEvent;
import org.bukkit.event.player.PlayerSwapHandItemsEvent;
import org.bukkit.inventory.InventoryView;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.PlayerInventory;
import org.bukkit.scheduler.BukkitTask;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;
import java.util.function.Supplier;

/**
 * Hotbar slots a hero suit takes over while it is worn (e.g. Iron Man's repulsor and thruster, Spider-Man's
 * web shooter). Each hero registers its slots with {@link #register}. Event handlers block the usual ways of moving items in or out, and a small repeating task re-asserts
 * the slot contents so anything the events miss (creative inventory, other plugins) is corrected.
 */
public final class SuitSlots implements Listener {

    private record Locked(int slot, Predicate<Player> active, Supplier<ItemStack> factory,
                          Predicate<ItemStack> matches, String message) {
    }

    private final List<Locked> locks = new ArrayList<>();
    private final MarvelHeroesPlugin plugin;
    private BukkitTask task;

    public SuitSlots(MarvelHeroesPlugin plugin) {
        this.plugin = plugin;
    }

    /**
     * Reserves hotbar {@code slot} for the item made by {@code factory} while {@code active} holds for a player.
     * {@code matches} must recognise exactly the items {@code factory} makes.
     */
    public void register(int slot, Predicate<Player> active, Supplier<ItemStack> factory,
                         Predicate<ItemStack> matches, String message) {
        locks.add(new Locked(slot, active, factory, matches, message));
    }

    /** True for any item that lives in a locked slot. */
    public boolean isLockedItem(ItemStack item) {
        for (Locked lock : locks) if (lock.matches().test(item)) return true;
        return false;
    }

    public void start() {
        task = Bukkit.getScheduler().runTaskTimer(plugin, () -> {
            for (Player p : Bukkit.getOnlinePlayers()) enforce(p);
        }, 1L, 2L);
    }

    public void shutdown() {
        if (task != null) task.cancel();
        for (Player p : Bukkit.getOnlinePlayers()) {
            for (Locked lock : locks) strip(p, lock, false);
        }
    }

    private void enforce(Player p) {
        PlayerInventory inv = p.getInventory();
        for (Locked lock : locks) {
            boolean on = lock.active().test(p);
            strip(p, lock, on);
            if (!on) continue;

            ItemStack current = inv.getItem(lock.slot());
            if (lock.matches().test(current)) continue;
            // Lock the slot first, then move whatever was there somewhere else (or drop it).
            inv.setItem(lock.slot(), lock.factory().get());
            if (current != null && !current.getType().isAir()) {
                inv.addItem(current).values()
                        .forEach(left -> p.getWorld().dropItemNaturally(p.getLocation(), left));
                p.sendActionBar(Component.text(lock.message(), NamedTextColor.AQUA));
            }
            p.playSound(p.getLocation(), Sound.BLOCK_BEACON_POWER_SELECT, 0.6f, 1.8f);
        }
    }

    /** Removes the lock's items everywhere except its own slot (when {@code keepSlot} is true). */
    private static void strip(Player p, Locked lock, boolean keepSlot) {
        PlayerInventory inv = p.getInventory();
        for (int i = 0; i < 36; i++) {
            if (keepSlot && i == lock.slot()) continue;
            if (lock.matches().test(inv.getItem(i))) inv.setItem(i, null);
        }
        if (lock.matches().test(inv.getItemInOffHand())) inv.setItemInOffHand(null);
        if (lock.matches().test(p.getItemOnCursor())) p.setItemOnCursor(null);
    }

    // ------------------------------------------------------------------ events

    @EventHandler(priority = EventPriority.LOWEST)
    public void onClick(InventoryClickEvent event) {
        if (!(event.getWhoClicked() instanceof Player p)) return;
        if (isLockedItem(event.getCurrentItem()) || isLockedItem(event.getCursor())) {
            event.setCancelled(true);
            return;
        }
        for (Locked lock : locks) {
            if (!lock.active().test(p)) continue;
            // A number key would swap something into the locked slot.
            if (event.getClick() == ClickType.NUMBER_KEY && event.getHotbarButton() == lock.slot()) {
                event.setCancelled(true);
                return;
            }
            if (event.getClickedInventory() instanceof PlayerInventory && event.getSlot() == lock.slot()) {
                event.setCancelled(true);
                return;
            }
        }
    }

    @EventHandler(priority = EventPriority.LOWEST)
    public void onDrag(InventoryDragEvent event) {
        if (!(event.getWhoClicked() instanceof Player p)) return;
        InventoryView view = event.getView();
        for (int raw : event.getRawSlots()) {
            if (!(view.getInventory(raw) instanceof PlayerInventory)) continue;
            int slot = view.convertSlot(raw);
            for (Locked lock : locks) {
                if (slot == lock.slot() && lock.active().test(p)) {
                    event.setCancelled(true);
                    return;
                }
            }
        }
    }

    @EventHandler(priority = EventPriority.LOWEST)
    public void onDrop(PlayerDropItemEvent event) {
        if (isLockedItem(event.getItemDrop().getItemStack())) event.setCancelled(true);
    }

    @EventHandler(priority = EventPriority.LOWEST)
    public void onSwapHands(PlayerSwapHandItemsEvent event) {
        if (isLockedItem(event.getMainHandItem()) || isLockedItem(event.getOffHandItem())) {
            event.setCancelled(true);
        }
    }

    @EventHandler(ignoreCancelled = true)
    public void onArmorStand(PlayerArmorStandManipulateEvent event) {
        if (isLockedItem(event.getPlayerItem())) event.setCancelled(true);
    }

    @EventHandler
    public void onDeath(PlayerDeathEvent event) {
        event.getDrops().removeIf(this::isLockedItem);
        event.getItemsToKeep().removeIf(this::isLockedItem);
    }
}
