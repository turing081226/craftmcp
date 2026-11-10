package io.github.craftmcp.debuggersample;

import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.command.RemoteConsoleCommandSender;

/** Fixed, side-effect-free RCON integration fixture; craftdebug stays console-only. */
public final class RconFixtureCommand implements CommandExecutor {
    private final DamageCalculator calculator = new DamageCalculator();

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        String run = args.length > 1 && args[1].matches("[A-Za-z0-9_-]{1,64}") ? args[1] : null;
        if (!(sender instanceof RemoteConsoleCommandSender)) {
            error(sender, run, "REMOTE_CONSOLE_REQUIRED");
            return true;
        }
        if (args.length != 4 || !args[0].equals("calculate") || run == null) {
            error(sender, run, "INVALID_ARGS");
            return true;
        }
        try {
            int base = Integer.parseInt(args[2]);
            int bonus = Integer.parseInt(args[3]);
            emit(sender, "status=started run_id=" + run + " base=" + base + " bonus=" + bonus);
            DamageCalculator.Result result = calculator.calculate(run, base, bonus);
            emit(sender, "status=ok run_id=" + run + " base=" + base + " bonus=" + bonus
                    + " damage=" + result.damage + " end=1");
        } catch (NumberFormatException ex) {
            error(sender, run, "INVALID_INTEGER");
        } catch (ArithmeticException ex) {
            error(sender, run, "INTEGER_OVERFLOW");
        }
        return true;
    }

    private static void error(CommandSender sender, String run, String code) {
        emit(sender, "status=error" + (run == null ? "" : " run_id=" + run) + " code=" + code + " end=1");
    }

    private static void emit(CommandSender sender, String fields) {
        // RCON implementations may concatenate sendMessage calls without separators.
        sender.sendMessage("CRAFTDEBUG_RCON v=1 " + fields + "\n");
    }
}
