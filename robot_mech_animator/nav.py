"""Navigation for the autopilot (pure Python, no bpy).

* :class:`NavMesh`  - any polygon mesh describing where the robot may walk.
  A* over the faces + funnel ("string pulling") gives the shortest corridor path.
* :class:`PathFollower` - pure-pursuit steering along a polyline, turns the path
  into stick commands for :class:`locomotion.Locomotor`.
"""
import heapq
import math
import random

from .locomotion import clamp, heading_of, wrap


def _area2(a, b, c):
    """Twice the signed area of triangle abc (2D)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])


def _point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


class NavMesh:
    def __init__(self, verts, faces):
        self.v = [tuple(map(float, p)) for p in verts]
        self.f = [tuple(f) for f in faces if len(f) >= 3]
        self.centers = []
        self.planes = []
        for f in self.f:
            pts = [self.v[i] for i in f]
            n = len(pts)
            self.centers.append(tuple(sum(p[k] for p in pts) / n for k in range(3)))
            # plane z = a x + b y + c fitted on the first three non-collinear points
            self.planes.append(self._plane(pts))
        edge_faces = {}
        for fi, f in enumerate(self.f):
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                edge_faces.setdefault((min(a, b), max(a, b)), []).append(fi)
        self.adj = [[] for _ in self.f]
        for (a, b), fs in edge_faces.items():
            for i in fs:
                for j in fs:
                    if i != j:
                        self.adj[i].append((j, (a, b)))
        # spatial hash for point location
        xs = [p[0] for p in self.v] or [0.0]
        ys = [p[1] for p in self.v] or [0.0]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        self.cell = max(span / max(1.0, math.sqrt(len(self.f))) * 1.5, 0.25)
        self.grid = {}
        for fi, f in enumerate(self.f):
            pts = [self.v[i] for i in f]
            x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
            y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
            for gx in range(int(math.floor(x0 / self.cell)), int(math.floor(x1 / self.cell)) + 1):
                for gy in range(int(math.floor(y0 / self.cell)), int(math.floor(y1 / self.cell)) + 1):
                    self.grid.setdefault((gx, gy), []).append(fi)

    @staticmethod
    def _plane(pts):
        p0 = pts[0]
        for i in range(1, len(pts) - 1):
            p1, p2 = pts[i], pts[i + 1]
            ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
            vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            if abs(nz) > 1e-9:
                a, b = -nx / nz, -ny / nz
                return a, b, p0[2] - a * p0[0] - b * p0[1]
        return 0.0, 0.0, sum(p[2] for p in pts) / len(pts)

    # ------------------------------------------------------------------ queries
    def locate(self, x, y, z_hint=None):
        best, best_dz = None, 1e18
        for fi in self.grid.get((int(math.floor(x / self.cell)), int(math.floor(y / self.cell))), ()):
            if _point_in_poly(x, y, [self.v[i] for i in self.f[fi]]):
                dz = 0.0 if z_hint is None else abs(self.height_on(fi, x, y) - z_hint)
                if dz < best_dz:
                    best, best_dz = fi, dz
        return best

    def height_on(self, fi, x, y):
        a, b, c = self.planes[fi]
        return a * x + b * y + c

    def height(self, x, y, z_hint=None):
        fi = self.locate(x, y, z_hint)
        return None if fi is None else self.height_on(fi, x, y)

    def nearest_face(self, x, y):
        best, bd = None, 1e18
        for fi, c in enumerate(self.centers):
            d = (c[0] - x) ** 2 + (c[1] - y) ** 2
            if d < bd:
                best, bd = fi, d
        return best

    def snap(self, x, y, z_hint=None):
        """Point on the navmesh closest to (x, y) (face centre if outside)."""
        fi = self.locate(x, y, z_hint)
        if fi is not None:
            return (x, y, self.height_on(fi, x, y)), fi
        fi = self.nearest_face(x, y)
        if fi is None:
            return None, None
        return self.centers[fi], fi

    def random_point(self, rng):
        fi = rng.randrange(len(self.f))
        return self.centers[fi]

    # ------------------------------------------------------------------ path finding
    def astar(self, f0, f1):
        if f0 is None or f1 is None:
            return None
        if f0 == f1:
            return [f0], []

        def h(i):
            a, b = self.centers[i], self.centers[f1]
            return math.hypot(a[0] - b[0], a[1] - b[1])
        openq = [(h(f0), 0.0, f0)]
        came = {f0: (None, None)}
        g = {f0: 0.0}
        while openq:
            _f, gc, cur = heapq.heappop(openq)
            if cur == f1:
                break
            if gc > g.get(cur, 1e18):
                continue
            c0 = self.centers[cur]
            for nb, edge in self.adj[cur]:
                c1 = self.centers[nb]
                ng = gc + math.hypot(c1[0] - c0[0], c1[1] - c0[1])
                if ng < g.get(nb, 1e18):
                    g[nb] = ng
                    came[nb] = (cur, edge)
                    heapq.heappush(openq, (ng + h(nb), ng, nb))
        if f1 not in came:
            return None
        faces, edges = [], []
        cur = f1
        while cur is not None:
            faces.append(cur)
            prev, edge = came[cur]
            if edge is not None:
                edges.append(edge)
            cur = prev
        return faces[::-1], edges[::-1]

    def find_path(self, start, goal):
        """List of (x, y, z) points from start to goal, or None if unreachable."""
        s, f0 = self.snap(start[0], start[1], start[2] if len(start) > 2 else None)
        e, f1 = self.snap(goal[0], goal[1], goal[2] if len(goal) > 2 else None)
        res = self.astar(f0, f1)
        if res is None:
            return None
        faces, edges = res
        portals = [(s, s)]
        for k, (a, b) in enumerate(edges):
            pa, pb = self.v[a], self.v[b]
            c0 = self.centers[faces[k]]
            # order the portal as (left, right) seen when leaving face k
            if _area2(c0, pa, pb) > 0:
                portals.append((pb, pa))
            else:
                portals.append((pa, pb))
        portals.append((e, e))
        pts = self._funnel(portals)
        out = []
        for p in pts:
            z = self.height(p[0], p[1], p[2])
            out.append((p[0], p[1], p[2] if z is None else z))
        return out

    @staticmethod
    def _funnel(portals):
        """String pulling through the portals (simple stupid funnel, standard CCW convention)."""
        pts = [portals[0][0]]
        apex = left = right = portals[0][0]
        ai = li = ri = 0
        i = 1
        n = len(portals)
        guard = 0
        while i < n and guard < 100000:
            guard += 1
            pl, pr = portals[i]
            # try to tighten the right side
            if _area2(apex, right, pr) >= 0.0:
                if apex == right or _area2(apex, left, pr) < 0.0:
                    right, ri = pr, i
                else:                                    # right crossed left: left is a corner
                    pts.append(left)
                    apex, ai = left, li
                    left = right = apex
                    li = ri = ai
                    i = ai + 1
                    continue
            # try to tighten the left side
            if _area2(apex, left, pl) <= 0.0:
                if apex == left or _area2(apex, right, pl) > 0.0:
                    left, li = pl, i
                else:                                    # left crossed right: right is a corner
                    pts.append(right)
                    apex, ai = right, ri
                    left = right = apex
                    li = ri = ai
                    i = ai + 1
                    continue
            i += 1
        end = portals[-1][0]
        if pts[-1] != end:
            pts.append(end)
        return pts


# =========================================================================== steering

class PathFollower:
    """Pure pursuit along a polyline of (x, y, z) points."""

    def __init__(self, pts, loop=False, lookahead=1.6, arrive=0.5):
        self.pts = [tuple(p) for p in pts]
        if loop and len(self.pts) > 2 and self.pts[0] != self.pts[-1]:
            self.pts.append(self.pts[0])
        self.loop = loop
        self.look = lookahead
        self.arrive = arrive
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.cum.append(self.cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        self.length = self.cum[-1]
        self.s = 0.0
        self.laps = 0
        self.seg = 0

    def point_at(self, s):
        if self.loop and self.length > 0:
            s %= self.length
        s = clamp(s, 0.0, self.length)
        i = max(0, min(len(self.pts) - 2, self._seg_for(s)))
        a, b = self.pts[i], self.pts[i + 1]
        L = self.cum[i + 1] - self.cum[i]
        u = 0.0 if L <= 1e-9 else (s - self.cum[i]) / L
        return tuple(a[k] + (b[k] - a[k]) * u for k in range(3))

    def _seg_for(self, s):
        lo, hi = 0, len(self.cum) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        return lo

    def project(self, x, y):
        """Advance the progress ``s`` to the closest point near the current one."""
        best, best_s = 1e18, self.s
        window = self.look * 2.0 + 1.0
        n = len(self.pts) - 1
        for i in range(n):
            if self.cum[i] > self.s + window and not self.loop:
                break
            a, b = self.pts[i], self.pts[i + 1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            u = 0.0 if L2 < 1e-12 else clamp(((x - a[0]) * dx + (y - a[1]) * dy) / L2, 0.0, 1.0)
            s = self.cum[i] + u * math.sqrt(L2)
            ds = s - self.s
            if self.loop and ds < -self.length / 2:
                ds += self.length
            if ds < -0.5 or ds > window:
                continue
            px, py = a[0] + dx * u, a[1] + dy * u
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best:
                best, best_s = d, s
        if self.loop and best_s < self.s - self.length / 2:
            self.laps += 1
        self.s = best_s
        return math.sqrt(best) if best < 1e18 else 0.0

    def remaining(self):
        return float("inf") if self.loop else max(0.0, self.length - self.s)

    def command(self, x, y, yaw, speed=1.0):
        """Returns (fwd, turn, done) stick values for the locomotor."""
        if self.length <= 1e-6:
            return 0.0, 0.0, True
        self.project(x, y)
        rem = self.remaining()
        if rem < self.arrive:
            return 0.0, 0.0, True
        tx, ty, _tz = self.point_at(self.s + min(self.look, rem if not self.loop else self.look))
        if math.hypot(tx - x, ty - y) < 1e-3:
            return 0.0, 0.0, True
        err = wrap(heading_of(tx - x, ty - y) - yaw)
        turn = clamp(err * 1.8, -1.0, 1.0)
        if abs(err) > math.radians(65):
            fwd = 0.0                                           # turn on the spot first
        else:
            fwd = speed * max(0.0, math.cos(err)) ** 2
        if not self.loop:
            fwd *= clamp(rem / 2.5, 0.25, 1.0)
        return fwd, turn, False


def wander_targets(nav, rng, n=6, min_dist=6.0):
    pts = []
    tries = 0
    while len(pts) < n and tries < n * 50:
        tries += 1
        p = nav.random_point(rng)
        if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= min_dist for q in pts):
            pts.append(p)
    return pts


__all__ = ["NavMesh", "PathFollower", "wander_targets", "random"]
