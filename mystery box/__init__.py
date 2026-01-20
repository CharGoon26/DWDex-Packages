from ballsdex.packages.mysterybox.cog import MysteryBox

async def setup(bot):
    await bot.add_cog(MysteryBox(bot))
