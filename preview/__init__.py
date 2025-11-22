from .cog import Preview

async def setup(bot):
    await bot.add_cog(Preview(bot))
