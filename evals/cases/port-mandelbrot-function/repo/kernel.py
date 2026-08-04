def mandelbrot_checksum(width: int, height: int, max_iter: int) -> int:
    """Return a deterministic work-weighted checksum of a Mandelbrot grid."""
    total = 0
    for row in range(height):
        cy = -1.2 + 2.4 * float(row) / float(height)
        for column in range(width):
            cx = -2.0 + 3.0 * float(column) / float(width)
            x = 0.0
            y = 0.0
            iteration = 0
            while x * x + y * y <= 4.0 and iteration < max_iter:
                next_x = x * x - y * y + cx
                y = 2.0 * x * y + cy
                x = next_x
                iteration += 1
            total += iteration * (row + 1) + column
    return total
