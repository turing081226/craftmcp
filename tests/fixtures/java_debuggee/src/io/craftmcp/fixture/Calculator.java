package io.craftmcp.fixture;

public final class Calculator {
    private final String label = "테스트 대상\n두 번째 줄";
    private final int[] samples = {4, 3, 12};
    private final Calculator self = this;
    private final String[] wide = java.util.Collections.nCopies(100, "한".repeat(6000)).toArray(String[]::new);

    public Calculator() {}

    public int calculate(int base, int multiplier) {
        int damage = base * multiplier;
        Calculator target = this;
        int[] values = samples;
        String large = "한".repeat(6000);
        System.out.println("RESULT damage=" + damage + " label=" + target.label.length()); // BREAKPOINT_AFTER_CALC
        return damage;
    }
}
