class Probe extends Info;
function int F(optional out array<int> A)
{
    return A.Length;
}
function G()
{
    F();
}
defaultproperties
{
}
