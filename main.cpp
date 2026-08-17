#include <iostream>

consteval int square(int x) {
    if (x < 0) throw "x must be non-negative";
    return x * x;
}

int main() {
    constexpr auto val = square(5);
    std::cout << "Square: " << val << std::endl;
    return 0;
}
