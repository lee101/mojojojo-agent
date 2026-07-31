# mojosub is a source checkout on this box, not an installed distribution.
# Link the package into the working tree so `import mojosub` resolves for the
# agent and for check.sh without mutating the system environment.
ln -sf /nvme0n1-disk/code/mojosub/mojosub mojosub
