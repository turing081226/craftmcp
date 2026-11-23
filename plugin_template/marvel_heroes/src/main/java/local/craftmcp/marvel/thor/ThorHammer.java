package local.craftmcp.marvel.thor;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextDecoration;
import org.bukkit.Material;
import org.bukkit.NamespacedKey;
import org.bukkit.inventory.ItemFlag;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.meta.ItemMeta;
import org.bukkit.persistence.PersistentDataContainer;
import org.bukkit.persistence.PersistentDataType;

import java.util.List;

/**
 * Mjolnir, a custom item (1.20.1 has no mace): an iron sword with its own model in the resource pack.
 * While the hammer is thrown, the same item stays in the player's inventory in a "thrown" state that
 * shows only a faint outline; right-clicking it calls the hammer back.
 */
public final class ThorHammer {

    public static final int MODEL = 7004;
    public static final int MODEL_THROWN = 7005;

    private static final NamespacedKey KEY = new NamespacedKey("marvelheroes", "mjolnir");
    private static final NamespacedKey THROWN = new NamespacedKey("marvelheroes", "mjolnir_thrown");

    private ThorHammer() {
    }

    public static ItemStack create() {
        ItemStack item = new ItemStack(Material.IRON_SWORD);
        ItemMeta meta = item.getItemMeta();
        meta.getPersistentDataContainer().set(KEY, PersistentDataType.BYTE, (byte) 1);
        meta.setUnbreakable(true);
        meta.addItemFlags(ItemFlag.HIDE_UNBREAKABLE, ItemFlag.HIDE_ATTRIBUTES);
        item.setItemMeta(meta);
        setThrown(item, false);
        return item;
    }

    public static boolean is(ItemStack item) {
        if (item == null || item.getType() != Material.IRON_SWORD || !item.hasItemMeta()) return false;
        return item.getItemMeta().getPersistentDataContainer().has(KEY, PersistentDataType.BYTE);
    }

    public static boolean isThrown(ItemStack item) {
        return is(item) && item.getItemMeta().getPersistentDataContainer().has(THROWN, PersistentDataType.BYTE);
    }

    /** Switches the item between "in hand" and "thrown" (outline model, different name). */
    public static void setThrown(ItemStack item, boolean thrown) {
        ItemMeta meta = item.getItemMeta();
        PersistentDataContainer pdc = meta.getPersistentDataContainer();
        if (thrown) {
            pdc.set(THROWN, PersistentDataType.BYTE, (byte) 1);
            meta.setCustomModelData(MODEL_THROWN);
            meta.displayName(plain("묠니르 (던짐) - 우클릭: 되돌아오기", NamedTextColor.GRAY));
        } else {
            pdc.remove(THROWN);
            meta.setCustomModelData(MODEL);
            meta.displayName(plain("묠니르", NamedTextColor.AQUA).decorate(TextDecoration.BOLD));
        }
        meta.lore(List.of(plain("우클릭: 던지기 / 던진 뒤 다시 우클릭: 되돌아오기", NamedTextColor.GRAY)));
        item.setItemMeta(meta);
    }

    /** The model shown on the flying hammer. */
    public static ItemStack flyingModel() {
        ItemStack item = new ItemStack(Material.IRON_SWORD);
        ItemMeta meta = item.getItemMeta();
        meta.setCustomModelData(MODEL);
        item.setItemMeta(meta);
        return item;
    }

    private static Component plain(String text, NamedTextColor color) {
        return Component.text(text, color).decoration(TextDecoration.ITALIC, false);
    }
}
