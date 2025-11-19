from .cog import Rarities

async def setup(bot):
    await bot.add_cog(Rarities(bot))