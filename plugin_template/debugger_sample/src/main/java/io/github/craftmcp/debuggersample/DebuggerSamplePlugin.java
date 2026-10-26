package io.github.craftmcp.debuggersample;

import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.command.ConsoleCommandSender;
import org.bukkit.plugin.java.JavaPlugin;

public final class DebuggerSamplePlugin extends JavaPlugin {
    private final DamageCalculator calculator = new DamageCalculator();

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!(sender instanceof ConsoleCommandSender)) {
            sender.sendMessage("CRAFTDEBUG status=error code=LOCAL_CONSOLE_REQUIRED");
            return true;
        }
        if (args.length != 4 || !args[0].equals("calculate") || !args[1].matches("[A-Za-z0-9_-]{1,64}")) {
            sender.sendMessage("CRAFTDEBUG status=error code=INVALID_ARGS usage=craftdebug_calculate_runid_base_bonus");
            return true;
        }
        try {
            int base = Integer.parseInt(args[2]);
            int bonus = Integer.parseInt(args[3]);
            sender.sendMessage("CRAFTDEBUG status=started run_id=" + args[1] + " base=" + base + " bonus=" + bonus);
            DamageCalculator.Result result = calculator.calculate(args[1], base, bonus);
            sender.sendMessage("CRAFTDEBUG status=ok run_id=" + result.runId + " damage=" + result.damage);
        } catch (NumberFormatException ex) {
            sender.sendMessage("CRAFTDEBUG status=error code=INVALID_INTEGER run_id=" + args[1]);
        } catch (ArithmeticException ex) {
            sender.sendMessage("CRAFTDEBUG status=error code=INTEGER_OVERFLOW run_id=" + args[1]);
        }
        return true;
    }
}
