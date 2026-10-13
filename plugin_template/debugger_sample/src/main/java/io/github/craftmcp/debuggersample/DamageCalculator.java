package io.github.craftmcp.debuggersample;

public final class DamageCalculator {
    public Result calculate(String runId, int base, int bonus) {
        int damage = Math.addExact(base, bonus);
        Result result = new Result(runId, base, bonus, damage);
        return result; // BREAKPOINT: damage and result have been assigned.
    }

    public static final class Result {
        public final String runId;
        public final int base;
        public final int bonus;
        public final int damage;
        public final int[] inputs;

        public Result(String runId, int base, int bonus, int damage) {
            this.runId = runId;
            this.base = base;
            this.bonus = bonus;
            this.damage = damage;
            this.inputs = new int[] {base, bonus};
        }
    }
}
